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
        """Инициализация API"""
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
            # Клонируем inbound из существующего
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

            # Создаем новый inbound
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
            # Получаем inbound для доступа к его клиентам через settings
            inbound = self.api.inbound.get_by_id(inbound_id)

            # Проверяем, есть ли inbound
            if not inbound:
                logger.warning(f"Inbound {inbound_id} not found")
                return []

            # Проверяем, есть ли настройки для inbound
            if not hasattr(inbound, "settings") or not inbound.settings:
                logger.debug(f"No settings found for inbound {inbound_id}")
                return []

            # Проверяем, есть ли клиенты в настройках inbound
            if not hasattr(inbound.settings, "clients") or not inbound.settings.clients:
                logger.debug(f"No clients found for inbound {inbound_id}")
                return []

            # Получаем клиентов из настроек inbound
            clients = inbound.settings.clients
            logger.debug(f"Retrieved {len(clients)} clients for inbound {inbound_id}")
            return clients
        except Exception as e:
            logger.error(f"Failed to get clients for inbound {inbound_id}: {e}", exc_info=True)
            return []

    def get_all_clients(self) -> List[Dict[str, Any]]:
        """Получить всех клиентов из всех inbound'ов с информацией об inbound"""
        # Создаем список для всех клиентов
        all_clients = []
        inbounds = self.get_all_inbounds()

        # Получаем все inbound'ы
        for inbound in inbounds:
            clients = self.get_clients_by_inbound(inbound.id)
            # Добавляем клиентов в список
            for client in clients:
                all_clients.append(
                    {
                        "client": client,
                        "inbound_id": inbound.id,
                        "inbound_remark": inbound.remark,
                        "inbound_port": inbound.port,
                    }
                )

        # Возвращаем список всех клиентов
        logger.debug(f"Retrieved {len(all_clients)} total clients")
        return all_clients

    def find_client_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Найти клиента по email во всех inbound'ах"""
        # Получаем всех клиентов из всех inbound'ов
        all_clients = self.get_all_clients()
        # Проверяем, есть ли клиент с таким email
        for client_info in all_clients:
            # Если клиент с таким email найден, возвращаем его информацию
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

            # Конвертируем GB в байты для 3x-ui
            total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0

            # Создаем нового клиента
            new_client = Client(
                id=str(uuid.uuid4()),
                email=email,
                enable=enable,
                total_gb=total_bytes,
                expiry_time=expiry_time,
            )

            # Добавляем нового клиента в inbound
            self.api.client.add(inbound_id, [new_client])
            logger.info(f"Created client {email} in inbound {inbound_id}")
            return new_client
        except Exception as e:
            logger.error(f"Failed to create client {email}: {e}")
            return None

    def get_client_config(self, inbound_id: int, email: str) -> Optional[str]:
        """Получить VLESS/VMESS конфиг для клиента"""
        try:
            # Получаем всех клиентов для конкретного inbound
            clients = self.get_clients_by_inbound(inbound_id)
            for client in clients:
                if client.email == email:
                    # Получаем inbound для построения URL конфигурации
                    inbound = self.get_inbound(inbound_id)
                    if not inbound:
                        return None

                    # Построить конфиг на основе протокола
                    config_url = self._build_config_url(inbound, client)
                    return config_url

            # Если клиент не найден, отправляем сообщение об ошибке
            logger.warning(f"Client {email} not found in inbound {inbound_id}")
            return None
        except Exception as e:
            logger.error(f"Failed to get config for {email}: {e}")
            return None

    def _build_config_url(self, inbound: Inbound, client: Client) -> str:
        """Построить VLESS/VMESS конфиг URL с правильными параметрами"""
        import urllib.parse
        from collections import OrderedDict

        # Базовый URL
        base_url = f"vless://{client.id}@{config.domain}:{inbound.port}"

        # Построить параметры запроса в определенном порядке (важно для некоторых клиентов)
        # Порядок: network type, encryption, security, pbk, fp, sni, sid, spx, flow
        params = OrderedDict()

        # Проверяем, есть ли настройки stream для inbound
        if hasattr(inbound, "stream_settings") and inbound.stream_settings:
            stream = inbound.stream_settings

            # Network type (tcp, ws, grpc, etc.)
            if hasattr(stream, "network") and stream.network:
                params["type"] = stream.network

            # Encryption (from settings.decryption)
            if hasattr(inbound, "settings") and inbound.settings:
                if hasattr(inbound.settings, "decryption") and inbound.settings.decryption:
                    params["encryption"] = inbound.settings.decryption

            # Security (tls, reality, none)
            if hasattr(stream, "security") and stream.security:
                params["security"] = stream.security

            # Reality settings (only if security is reality)
            # pbk, fp, sni, sid, spx - reality settings only if security is reality
            if hasattr(stream, "security") and stream.security == "reality":
                reality = getattr(stream, "reality_settings", None)
                if reality:
                    if isinstance(reality, dict):
                        r_settings = reality.get("settings", {})
                        serverNames = reality.get("serverNames", [])
                        shortIds = reality.get("shortIds", [])
                    else:
                        r_settings = getattr(reality, "settings", {})
                        serverNames = getattr(reality, "serverNames", [])
                        shortIds = getattr(reality, "shortIds", [])

                    if isinstance(r_settings, dict):
                        params["pbk"] = r_settings.get("publicKey")
                        params["fp"] = r_settings.get("fingerprint")
                        params["spx"] = r_settings.get("spiderX")
                    else:
                        params["pbk"] = getattr(r_settings, "publicKey", None)
                        params["fp"] = getattr(r_settings, "fingerprint", None)
                        params["spx"] = getattr(r_settings, "spiderX", None)

                    # Приоритет для SNI и Short ID из списков, если они заполнены
                    if serverNames and len(serverNames) > 0:
                        params["sni"] = serverNames[0]
                    if shortIds and len(shortIds) > 0:
                        params["sid"] = shortIds[0]

            # Flow (для XTLS) - если есть
            if hasattr(client, "flow") and client.flow:
                params["flow"] = client.flow

        # Построить строку запроса
        query_string = urllib.parse.urlencode(params)

        # Fragment (remark) - комментарий к ключу
        fragment = f"{inbound.remark}-{client.email}"

        # Complete URL - полная URL
        full_url = f"{base_url}?{query_string}#{urllib.parse.quote(fragment)}"
        logger.info(f"Generated VLESS config for {client.email}: {full_url}")

        # Возвращаем полную URL
        return full_url

    def get_subscription_url(self, email: str) -> str:
        """Получить subscription URL для клиента"""
        # Формат subscription 3x-ui
        return f"https://{config.domain}:{config.subscription_port}/{email}"

    def update_client_traffic(self, inbound_id: int, email: str, total_gb: int, expiry_time: int):
        """Обновить лимиты трафика клиента"""
        try:
            # Конвертируем GB в байты для 3x-ui
            total_bytes = total_gb * 1024 * 1024 * 1024 if total_gb > 0 else 0

            # Получаем всех клиентов для конкретного inbound
            clients = self.get_clients_by_inbound(inbound_id)
            target_client = None
            for client in clients:
                if client.email == email:
                    target_client = client
                    break

            # Проверяем, есть ли клиент с таким email
            if not target_client:
                logger.error(f"Client {email} not found")
                return False

            # Обновляем клиента
            target_client.total_gb = total_bytes
            target_client.expiry_time = expiry_time

            # Обновляем клиента в inbound
            self.api.client.update(inbound_id, target_client.id, target_client)
            logger.info(f"Updated traffic for {email}")
            return True
        except Exception as e:
            logger.error(f"Failed to update client {email}: {e}")
            return False

    def delete_client(self, inbound_id: int, client_id: str):
        """Удалить клиента из inbound"""
        try:
            # Удаляем клиента из inbound
            self.api.client.delete(inbound_id, client_id)
            logger.info(f"Deleted client {client_id} from inbound {inbound_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete client {client_id}: {e}")
            return False

    # ===== Статистика =====

    def get_client_stats(self, email: str) -> Optional[Dict[str, Any]]:
        """Получить статистику трафика для клиента"""
        # Получаем информацию о клиенте
        client_info = self.find_client_by_email(email)
        # Проверяем, есть ли клиент с таким email
        if not client_info:
            return None

        # Получаем клиента
        client = client_info["client"]
        # Возвращаем статистику
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
