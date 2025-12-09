import os, time, uuid, urllib.parse, contextlib
import logging
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

log = logging.getLogger(__name__)

class SeleniumWhatsAppSender:
    def __init__(self, profile_dir: str, timeout: int = 20,
                 delivery_wait_sec: float = 8.0,
                 stabilize_after_send_sec: float = 1.0):
        os.environ.setdefault("WDM_LOG_LEVEL", "0")
        logging.getLogger("WDM").setLevel(logging.CRITICAL)
        logging.getLogger("webdriver_manager").setLevel(logging.CRITICAL)

        self.profile_dir = profile_dir
        self.profile_name = os.getenv("CHROME_PROFILE_NAME", "")
        self.timeout = timeout
        self.delivery_wait_sec = max(0.0, delivery_wait_sec)
        self.stabilize_after_send_sec = max(0.0, stabilize_after_send_sec)
        self.service = None
        self.driver = self._build_driver()

    def _build_driver(self):
        opts = Options()
        if self.profile_dir:
            opts.add_argument(f"--user-data-dir={self.profile_dir}")
        if self.profile_name:
            opts.add_argument(f"--profile-directory={self.profile_name}")
        if os.getenv("HEADLESS", "false").lower() in ("1", "true", "yes"):
            opts.add_argument("--headless=new")

        opts.add_argument("--disable-logging")
        opts.add_argument("--log-level=3")
        opts.add_argument("--silent")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-notifications")
        opts.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
        opts.add_experimental_option("useAutomationExtension", False)

        # Selenium 4: log_path silencia driver
        self.service = Service(ChromeDriverManager().install(), log_path=os.devnull)

        with open(os.devnull, "w") as devnull, \
             contextlib.redirect_stdout(devnull), \
             contextlib.redirect_stderr(devnull):
            driver = webdriver.Chrome(service=self.service, options=opts)

        driver.set_window_size(1280, 900)
        driver.set_page_load_timeout(self.timeout)
        return driver

    def _rebuild_driver(self):
        try:
            self.driver.quit()
        except Exception:
            pass
        self.driver = self._build_driver()

  
    def _ensure_logged_in(self):
        self.driver.get("https://web.whatsapp.com")
        WebDriverWait(self.driver, self.timeout).until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "header")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "canvas[aria-label='Scan me!']"))
            )
        )
        time.sleep(0.5)

    def _textbox(self):
        return WebDriverWait(self.driver, self.timeout + 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='textbox']"))
        )

    def _fill_text_if_missing(self, body: str):
        box = self._textbox()
        current = (box.text or "").strip()
        if current != (body or "").strip():
            self.driver.execute_script("""
                const el = arguments[0], txt = arguments[1];
                el.focus();
                const sel = window.getSelection(); sel.removeAllRanges();
                const r = document.createRange(); r.selectNodeContents(el);
                sel.addRange(r);
                document.execCommand('insertText', false, txt);
                el.dispatchEvent(new InputEvent('input', {bubbles:true}));
            """, box, body)
            time.sleep(0.1)

    def _click_send_or_enter(self):
        btn = self.driver.execute_script("""
            const q = (s)=>document.querySelector(s);
            return q("button[aria-label='Send']") 
                || q("button[aria-label='Enviar']") 
                || (q("span[data-icon='send']") && q("span[data-icon='send']").closest('button'))
                || q("div[role='button'][aria-label='Send']")
                || q("div[role='button'][data-tab='10']");
        """)
        if btn:
            self.driver.execute_script("arguments[0].click();", btn)
        else:
            box = self._textbox()
            box.send_keys(Keys.ENTER)

    def _count_outgoing(self) -> int:
        return len(self.driver.find_elements(By.CSS_SELECTOR, "div.message-out"))

    def _last_outgoing_bubble(self):
        bubbles = self.driver.find_elements(By.CSS_SELECTOR, "div.message-out")
        return bubbles[-1] if bubbles else None

    def _wait_sent_confirmation(self, before_count: int, timeout_extra: int = 0):
        WebDriverWait(self.driver, self.timeout + 15 + timeout_extra).until(
            lambda d: self._count_outgoing() > before_count
        )

    def _wait_delivery_icon(self, bubble, wait_sec: float):
        """
        Espera a que aparezca el icono de estado (msg-check/dblcheck).
        No lanza excepción hacia afuera: si no aparece, simplemente sigue.
        """
        if wait_sec <= 0 or not bubble:
            return False
        try:
            WebDriverWait(self.driver, max(1, int(wait_sec))).until(
                lambda d: bubble.find_elements(By.CSS_SELECTOR, "span[data-icon^='msg-']")
            )
            return True
        except Exception:
            return False

    def get_last_delivery_status(self) -> str:
        try:
            b = self._last_outgoing_bubble()
            if not b:
                return "desconocido"
            el = b.find_element(By.CSS_SELECTOR, "span[data-icon^='msg-']")
            icon = (el.get_attribute("data-icon") or "").strip()
            if icon == "msg-check":
                return "enviado"     
            if icon == "msg-dblcheck":
                return "entregado"  
            if icon == "msg-dblcheck-ack":
                return "leido"      
            return "desconocido"
        except Exception:
            return "desconocido"

    def send_text(self, phone_e164: str, body: str) -> tuple[str, str]:
        """
        Devuelve (msg_id, delivery_status)
        Éxito = aparece una NUEVA burbuja saliente.
        Si ocurre excepción después de generar la burbuja, NO reintenta (evita duplicados).
        """
        last_exc = None
        for attempt in range(1, 3):
            try:
                self._ensure_logged_in()
                url = f"https://web.whatsapp.com/send?phone={phone_e164}&text={urllib.parse.quote(body or '')}"
                self.driver.get(url)

                WebDriverWait(self.driver, self.timeout + 20).until(
                    EC.any_of(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='textbox']")),
                        EC.presence_of_element_located((By.XPATH, "//div[contains(@data-animate-modal-body,'true')]"))
                    )
                )

                try:
                    modal = self.driver.find_element(By.XPATH, "//div[contains(@data-animate-modal-body,'true')]")
                    if modal:
                        WebDriverWait(self.driver, self.timeout + 15).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='textbox']"))
                        )
                except NoSuchElementException:
                    pass

                self._fill_text_if_missing(body or "")
                before = self._count_outgoing()

                self._click_send_or_enter()

                self._wait_sent_confirmation(before)

                bubble = self._last_outgoing_bubble()
                _ = self._wait_delivery_icon(bubble, self.delivery_wait_sec)

                if self.stabilize_after_send_sec > 0:
                    time.sleep(self.stabilize_after_send_sec)

                status = self.get_last_delivery_status()
                return (f"selenium-{uuid.uuid4().hex[:12]}", status)

            except WebDriverException as e:
                try:
                    after = self._count_outgoing()
             
                    if after > 0:
                        status = self.get_last_delivery_status()
                        return (f"selenium-{uuid.uuid4().hex[:12]}", status)
                except Exception:
                    pass
                last_exc = e
                self._rebuild_driver()
                time.sleep(0.8)

            except Exception as e:
                try:
                    after = self._count_outgoing()
                    if after > 0:
                        status = self.get_last_delivery_status()
                        return (f"selenium-{uuid.uuid4().hex[:12]}", status)
                except Exception:
                    pass
                last_exc = e
                time.sleep(0.8)

        raise last_exc if last_exc else RuntimeError("Fallo desconocido en send_text()")

    def close(self):
        try:
            if self.driver:
                self.driver.quit()
        except Exception:
            pass
        try:
            if self.service:
                self.service.stop()
        except Exception:
            pass
#holaa