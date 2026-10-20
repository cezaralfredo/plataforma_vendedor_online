import time
from collections import OrderedDict
from threading import Lock


class SentMessageTracker:
    """
    Rastreia identificadores de mensagens enviadas pelo bot/sistema via WhatsApp Gateway.
    Permite diferenciar, nos webhooks de eco (fromMe: true), mensagens disparadas
    pela própria API daquelas digitadas por um atendente humano no app WhatsApp Business.
    """

    _instance: "SentMessageTracker | None" = None
    _lock = Lock()

    def __init__(self, max_size: int = 5000, ttl_seconds: int = 600):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._sent_ids: OrderedDict[str, float] = OrderedDict()
        self._instance_lock = Lock()

    @classmethod
    def get_instance(cls) -> "SentMessageTracker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def record(self, message_id: str) -> None:
        """Registra um ID de mensagem disparado pelo sistema."""
        if not message_id:
            return
        clean_id = str(message_id).strip()
        now = time.time()
        with self._instance_lock:
            self._sent_ids[clean_id] = now
            while len(self._sent_ids) > self.max_size:
                self._sent_ids.popitem(last=False)

    def is_bot_message(self, message_id: str) -> bool:
        """Verifica se o ID da mensagem foi registrado recentemente pela própria API."""
        if not message_id:
            return False
        clean_id = str(message_id).strip()
        now = time.time()
        with self._instance_lock:
            if clean_id in self._sent_ids:
                timestamp = self._sent_ids[clean_id]
                if (now - timestamp) <= self.ttl_seconds:
                    return True
                else:
                    self._sent_ids.pop(clean_id, None)
            return False

    def clear(self) -> None:
        """Limpa o cache (usado principalmente em testes)."""
        with self._instance_lock:
            self._sent_ids.clear()
