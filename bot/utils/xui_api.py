"""
Обёртка для 3x-ui API
Предоставляет высокоуровневый интерфейс для взаимодействия с панелью 3x-ui
"""

import logging
import requests
import urllib3
from typing import List, Dict, Any, Optional
import py3xui
from py3xui.inbound import Inbound
from py3xui.client import Client

from bot.config import config

logger = logging.getLogger(__name__)

# Отключение проверки SSL сертификатов (для самоподписанных сертификатов)
if not config.xui_use_ssl_cert:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    # Monkey-patch requests.Session для отключения проверки SSL
    _original_request = requests.Session.request

    def _patched_request(self, *args, **kwargs):
        kwargs["verify"] = False
        return _original_request(self, *args, **kwargs)

    requests.Session.request = _patched_request
    logger.info("Проверка SSL сертификатов отключена для py3xui")


class XUIApi:
    """Обёртка вокруг библиотеки py3xui с обработкой ошибок"""

    def __init__(self):
        self.api = py3xui.Api(
            host=config.xui_host,
            username=config.xui_username,
            password=config.xui_password,
        )
        self._login()

    def _login(self):
        """Аутентификация в панели 3x-ui"""
        try:
            self.api.login()
            logger.info(f"Successfully logged in to 3x-ui at {config.xui_host}")
        except Exception as e:
            logger.error(f"Failed to login to 3x-ui: {e}")
            raise

    # ===== Операции с Inbound =====

    def get_all_inbounds(self) -> List[Inbound]:
        """Получить список всех inbound'ов"""
        try:
            inbounds = self.api.inbound.get_list()
            logger.debug(f"Retrieved {len(inbounds)} inbounds")
            return inbounds
        except Exception as e:
            logger.error(f"Failed to get inbounds: {e}")
            return []

    def get_inbound(self, inbound_id: int) -> Optional[Inbound]:
        """Получить конкретный inbound по ID"""
        try:
            inbound = self.api.inbound.get_by_id(inbound_id)
            return inbound
        except Exception as e:
            logger.error(f"Failed to get inbound {inbound_id}: {e}")
            return None

    def create_inbound_from_template(
        self, template_inbound: Inbound, new_remark: str, new_port: int
    ) -> Optional[Inbound]:
        """Создать новый inbound клонированием существующего"""
        try:
            # Clone settings from template
            new_inbound = Inbound(
                enable=True,
                remark=new_remark,
                listen=template_inbound.listen,
                port=new_port,
                protocol=template_inbound.protocol,
                settings=template_inbound.settings,
                stream_settings=template_inbound.stream_settings,
                sniffing=template_inbound.sniffing,
                allocate=template_inbound.allocate,
            )

            result = self.api.inbound.add(new_inbound)
            logger.info(f"Created new inbound '{new_remark}' on port {new_port}")
            return result
        except Exception as e:
            logger.error(f"Failed to create inbound: {e}")
            return None

    # ===== Операции с клиентами =====

    def get_clients_by_inbound(self, inbound_id: int) -> List[Client]:
        """Получить всех клиентов для конкретного inbound"""
        try:
            # Get inbound to access its clients via settings
            inbound = self.api.inbound.get_by_id(inbound_id)

            if not inbound:
                logger.warning(f"Inbound {inbound_id} not found")
                return []

            # Clients are in inbound.settings.clients, not client_stats
            if not hasattr(inbound, "settings") or not inbound.settings:
                logger.debug(f"No settings found for inbound {inbound_id}")
                return []

            if not hasattr(inbound.settings, "clients") or not inbound.settings.clients:
                logger.debug(f"No clients found for inbound {inbound_id}")
                return []

            clients = inbound.settings.clients
            logger.debug(f"Retrieved {len(clients)} clients for inbound {inbound_id}")
            return clients
        except Exception as e:
            logger.error(
                f"Failed to get clients for inbound {inbound_id}: {e}", exc_info=True
            )
            return []

    def get_all_clients(self) -> List[Dict[str, Any]]:
        """Получить всех клиентов из всех inbound'ов с информацией об inbound"""
        all_clients = []
        inbounds = self.get_all_inbounds()

        for inbound in inbounds:
            clients = self.get_clients_by_inbound(inbound.id)
            for client in clients:
                all_clients.append(
                    {
                        "client": client,
                        "inbound_id": inbound.id,
                        "inbound_remark": inbound.remark,
                        "inbound_port": inbound.port,
                    }
                )

        logger.debug(f"Retrieved {len(all_clients)} total clients")
        return all_clients

    def find_client_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Найти клиента по email во всех inbound'ах"""
        all_clients = self.get_all_clients()
        for client_info in all_clients:
            if client_info["client"].email == email:
                return client_info
        return None

    def create_client(
        self,
        inbound_id: int,
        email: str,
        total_gb: int = 0,
        expiry_time: int = 0,
        enable: bool = True,
    ) -> Optional[Client]:
        """
        Создать нового клиента в указанном inbound

        Args:
            inbound_id: ID целевого inbound
            email: Email клиента (уникальный идентификатор)
            total_gb: Лимит трафика в GB (0 = безлимит)
            expiry_time: Timestamp истечения в миллисекундах (0 = бессрочно)
            enable: Включить клиента сразу

        Returns:
            Созданный клиент или None при ошибке
        """
        try:
            import uuid

            # Convert GB to bytes for 3x-ui
            total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0

            new_client = Client(
                id=str(uuid.uuid4()),
                email=email,
                enable=enable,
                total_gb=total_bytes,
                expiry_time=expiry_time,
            )

            self.api.client.add(inbound_id, [new_client])
            logger.info(f"Created client {email} in inbound {inbound_id}")
            return new_client
        except Exception as e:
            logger.error(f"Failed to create client {email}: {e}")
            return None

    def get_client_config(self, inbound_id: int, email: str) -> Optional[str]:
        """Получить VLESS/VMESS конфиг для клиента"""
        try:
            clients = self.get_clients_by_inbound(inbound_id)
            for client in clients:
                if client.email == email:
                    # Get inbound to construct config URL
                    inbound = self.get_inbound(inbound_id)
                    if not inbound:
                        return None

                    # Construct config based on protocol
                    # This is simplified - actual implementation depends on protocol
                    config_url = self._build_config_url(inbound, client)
                    return config_url

            logger.warning(f"Client {email} not found in inbound {inbound_id}")
            return None
        except Exception as e:
            logger.error(f"Failed to get config for {email}: {e}")
            return None

    def _build_config_url(self, inbound: Inbound, client: Client) -> str:
        """Построить VLESS/VMESS конфиг URL с правильными параметрами"""
        import urllib.parse
        from collections import OrderedDict

        # Base URL
        base_url = f"vless://{client.id}@{config.domain}:{inbound.port}"

        # Build query parameters in specific order (important for some clients)
        # Order: type, encryption, security, pbk, fp, sni, sid, spx, flow
        params = OrderedDict()

        if hasattr(inbound, "stream_settings") and inbound.stream_settings:
            stream = inbound.stream_settings

            # 1. Network type (tcp, ws, grpc, etc.)
            if hasattr(stream, "network") and stream.network:
                params["type"] = stream.network

            # 2. Encryption (from settings.decryption)
            if hasattr(inbound, "settings") and inbound.settings:
                if (
                    hasattr(inbound.settings, "decryption")
                    and inbound.settings.decryption
                ):
                    params["encryption"] = inbound.settings.decryption

            # 3. Security (tls, reality, none)
            if hasattr(stream, "security") and stream.security:
                params["security"] = stream.security

            # Reality settings (only if security is reality)
            if hasattr(stream, "reality_settings") and stream.reality_settings:
                reality = stream.reality_settings

                # 4. Public key (pbk)
                if hasattr(reality, "settings") and reality.settings:
                    if (
                        "publicKey" in reality.settings
                        and reality.settings["publicKey"]
                    ):
                        params["pbk"] = reality.settings["publicKey"]

                    # 5. Fingerprint (fp)
                    if (
                        "fingerprint" in reality.settings
                        and reality.settings["fingerprint"]
                    ):
                        params["fp"] = reality.settings["fingerprint"]

                # 6. Server name (sni)
                if (
                    hasattr(reality, "serverNames")
                    and reality.serverNames
                    and len(reality.serverNames) > 0
                ):
                    params["sni"] = reality.serverNames[0]
                elif hasattr(reality, "settings") and reality.settings:
                    if (
                        "serverName" in reality.settings
                        and reality.settings["serverName"]
                    ):
                        params["sni"] = reality.settings["serverName"]

                # 7. Short ID (sid)
                if (
                    hasattr(reality, "shortIds")
                    and reality.shortIds
                    and len(reality.shortIds) > 0
                ):
                    params["sid"] = reality.shortIds[0]

                # 8. Spider X (spx)
                if hasattr(reality, "settings") and reality.settings:
                    if "spiderX" in reality.settings and reality.settings["spiderX"]:
                        params["spx"] = reality.settings["spiderX"]

            # 9. Flow (for XTLS) - if present
            if hasattr(client, "flow") and client.flow:
                params["flow"] = client.flow

        # Build query string
        query_string = urllib.parse.urlencode(params)

        # Fragment (remark)
        fragment = f"{inbound.remark}-{client.email}"

        # Complete URL
        full_url = f"{base_url}?{query_string}#{urllib.parse.quote(fragment)}"

        # Debug logging
        logger.debug(f"Generated VLESS URL params: {params}")

        return full_url

    def get_subscription_url(self, email: str) -> str:
        """Получить subscription URL для клиента"""
        # Формат subscription 3x-ui
        return f"https://{config.domain}:{config.subscription_port}/{email}"

    def update_client_traffic(
        self, inbound_id: int, email: str, total_gb: int, expiry_time: int
    ):
        """Обновить лимиты трафика клиента"""
        try:
            total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0

            # Get existing client
            clients = self.get_clients_by_inbound(inbound_id)
            target_client = None
            for client in clients:
                if client.email == email:
                    target_client = client
                    break

            if not target_client:
                logger.error(f"Client {email} not found")
                return False

            # Update client
            target_client.total_gb = total_bytes
            target_client.expiry_time = expiry_time

            self.api.client.update(inbound_id, target_client.id, target_client)
            logger.info(f"Updated traffic for {email}")
            return True
        except Exception as e:
            logger.error(f"Failed to update client {email}: {e}")
            return False

    def delete_client(self, inbound_id: int, client_id: str):
        """Удалить клиента из inbound"""
        try:
            self.api.client.delete(inbound_id, client_id)
            logger.info(f"Deleted client {client_id} from inbound {inbound_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete client {client_id}: {e}")
            return False

    # ===== Статистика =====

    def get_client_stats(self, email: str) -> Optional[Dict[str, Any]]:
        """Получить статистику трафика для клиента"""
        client_info = self.find_client_by_email(email)
        if not client_info:
            return None

        client = client_info["client"]
        return {
            "email": client.email,
            "up": client.up if hasattr(client, "up") else 0,
            "down": client.down if hasattr(client, "down") else 0,
            "total": client.total_gb,
            "expiry_time": client.expiry_time,
            "enable": client.enable,
            "inbound_remark": client_info["inbound_remark"],
        }


# Global API instance
xui_api = XUIApi()
