from abc import ABC, abstractmethod

class WhatsAppSender(ABC):
    @abstractmethod
    def send_text(self, phone_e164: str, body: str) -> str:
        raise NotImplementedError
    @abstractmethod
    def close(self):
        pass
