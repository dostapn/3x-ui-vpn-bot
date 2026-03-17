"""
Обёртка для 3x-ui API
Предоставляет высокоуровневый интерфейс для взаимодействия с панелью 3x-ui
"""

import logging
import uuid
from functools import wraps
from typing import List, Dict, Any, Optional
from urllib.parse import urlencode, quote

import requests
import urllib3
import py3xui
from py3xui.inbound import Inbound
from py3xui.client import Client

from bot.config import config

logger = logging.getLogger(__name__)


def handle_auth_errors(func):
    """Декоратор для перехвата ошибок аутентификации и переавторизации"""

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except requests.exceptions.HTTPError as e:
            # Проверяем, если это ошибка аутентификации (401, 403)
            if e.response.status_code in (401, 403):
                logger.warning(
                    f"Authentication error ({e.response.status_code}), attempting re-login..."
                )
                try:
                    self._login()
                    logger.info("Re-login successful, retrying operation...")
                    return func(self, *args, **kwargs)
                except Exception as retry_error:
                    logger.error(f"Re-login failed: {retry_error}", exc_info=True)
                    raise
            else:
                # Для других HTTP ошибок просто пробрасываем дальше
                raise
        except Exception as e:
            # Для других типов исключений логируем и пробрасываем
            raise

    return wrapper


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

    @handle_auth_errors
    def get_all_inbounds(self) -> List[Inbound]:
        """Получить список всех inbound'ов"""
        try:
            inbounds = self.api.inbound.get_list()
            logger.debug(f"Retrieved {len(inbounds)} inbounds from API")
            return inbounds
        except Exception as e:
            logger.error(f"Failed to get inbounds: {e}", exc_info=True)
            return []

    @handle_auth_errors
    def get_inbound(self, inbound_id: int) -> Optional[Inbound]:
        """Получить конкретный inbound по ID"""
        try:
            return self.api.inbound.get_by_id(inbound_id)
        except Exception as e:
            logger.error(f"Failed to get inbound {inbound_id}: {e}", exc_info=True)
            return None

    def create_inbound_from_template(
        self, template_id: int, new_remark: str
    ) -> Optional[Dict[str, Any]]:
        """Создать новый inbound клонированием существующего"""
        try:
            # Получаем шаблон inbound по ID
            template = self.get_inbound(template_id)
            if not template:
                logger.error(f"Template inbound {template_id} not found")
                return None

            # Убеждаемся, что сессия активна
            self._ensure_authenticated()

            # Клонируем inbound из существующего
            new_inbound = Inbound(
                enable=True,
                remark=new_remark,
                listen=template.listen,
                port=0,  # Позволим 3x-ui автоматически выбрать порт
                protocol=template.protocol,
                settings=template.settings,
                stream_settings=template.stream_settings,
                sniffing=template.sniffing,
                allocate=template.allocate,
            )

            # Создаем новый inbound
            result = self.api.inbound.add(new_inbound)
            logger.info(f"Created new inbound '{new_remark}' from template {template_id}")

            # Возвращаем словарь с информацией о новом inbound
            return {
                "id": result.id,
                "remark": result.remark,
                "port": result.port,
            }
        except Exception as e:
            logger.error(f"Failed to create inbound from template: {e}", exc_info=True)
            return None

    # ===== Операции с клиентами =====

    @handle_auth_errors
    def get_clients_by_inbound(self, inbound_id: int) -> List[Client]:
        """Получить всех клиентов для конкретного inbound'а"""
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

    def find_client_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """Найти клиента по email во всех inbound'ах"""
        try:
            # Получаем все inbound'ы один раз
            inbounds = self.get_all_inbounds()

            # Проходим по каждому inbound
            for inbound in inbounds:
                # Получаем клиентов ТОЛЬКО этого inbound
                clients = self.get_clients_by_inbound(inbound.id)

                # Ищем клиента по email в текущем inbound
                for client in clients:
                    if client.email == email:
                        # Нашли! Возвращаем информацию и сразу выходим
                        return {
                            "client": client,
                            "inbound_id": inbound.id,
                            "inbound_remark": inbound.remark,
                            "inbound_port": inbound.port,
                        }

            # Клиент не найден ни в одном inbound
            logger.debug(f"Client with email {email} not found in any inbound")
            return None
        except Exception as e:
            logger.error(f"Failed to find client by email {email}: {e}", exc_info=True)
            return None

    def create_client(
        self,
        inbound_id: int,
        email: str,
        total_gb: int = 0,
        expiry_time: int = 0,
        enable: bool = True,
    ) -> Optional[Client]:
        """Создать клиента в inbound"""
        try:
            # Конвертируем GB в байты для 3x-ui
            # (0 значит безлимит - не конвертируем)
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
            logger.error(f"Failed to create client {email}: {e}", exc_info=True)
            return None

    def get_client_config(self, inbound_id: int, email: str) -> Optional[str]:
        """Получить VLESS конфиг URL для клиента"""
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
        # Базовый URL формат: vless://uuid@domain:port
        base_url = f"vless://{client.id}@{config.domain}:{inbound.port}"

        # Параметры в порядке вставки (важно!) (Python 3.7+ сохраняет порядок в dict)
        # Порядок: type, encryption, security, pbk, fp, sni, sid, spx, flow
        params = {}

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
                    if serverNames:
                        params["sni"] = serverNames[0]
                    if shortIds:
                        params["sid"] = shortIds[0]

            # Flow (для XTLS) - если есть
            if hasattr(client, "flow") and client.flow:
                params["flow"] = client.flow

        # Строим строку параметров
        query_string = urlencode(params)

        # Fragment - это комментарий/название для ключа в клиенте
        fragment = f"{inbound.remark}-{client.email}"

        # Полная VLESS ссылка с параметрами и фрагментом
        full_url = f"{base_url}?{query_string}#{quote(fragment)}"
        logger.info(f"Generated VLESS config for {client.email}: {full_url}")

        # Возвращаем полную URL
        return full_url

    def get_subscription_url(self, email: str) -> str:
        """Получить subscription URL"""
        # Формат subscription 3x-ui
        return f"https://{config.domain}:{config.subscription_port}/{email}"

    def delete_client(self, inbound_id: int, client_id: str):
        """Удалить клиента"""
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
        """Получить статистику трафика"""
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
