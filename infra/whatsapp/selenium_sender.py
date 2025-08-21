import os, time, uuid, urllib.parse
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager

class SeleniumWhatsAppSender:
    def __init__(self, profile_dir: str, timeout: int = 20):
        self.profile_dir = profile_dir
        self.profile_name = os.getenv("CHROME_PROFILE_NAME", "")
        self.timeout = timeout
        self.driver = self._build_driver()

    def _build_driver(self):
        opts = Options()
        if self.profile_dir:
            opts.add_argument(f"--user-data-dir={self.profile_dir}")
        if self.profile_name:
            opts.add_argument(f"--profile-directory={self.profile_name}")
        if os.getenv("HEADLESS", "false").lower() in ("1","true","yes"):
            opts.add_argument("--headless=new")
        opts.add_argument("--disable-notifications")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=opts)
        driver.set_window_size(1280, 900)
        driver.set_page_load_timeout(self.timeout)
        return driver

    def _ensure_logged_in(self):
        self.driver.get("https://web.whatsapp.com")
        WebDriverWait(self.driver, self.timeout).until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, "header")),
                EC.presence_of_element_located((By.CSS_SELECTOR, "canvas[aria-label='Scan me!']"))
            )
        )
        time.sleep(1.0)

    def _textbox(self):
        # Editor clásico (role='textbox'); en la UI nueva sigue existiendo como wrapper
        return WebDriverWait(self.driver, self.timeout+10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='textbox']"))
        )

    def _is_editor_empty(self) -> bool:
        try:
            txt = (self._textbox().text or "").strip()
            return txt == ""
        except Exception:
            return False

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
            time.sleep(0.2)

    def _count_outgoing(self) -> int:
        return len(self.driver.find_elements(By.CSS_SELECTOR, "div.message-out"))

    def _wait_sent_confirmation(self, before_count: int, timeout_extra: int = 0):
        """Espera a que aumenten las burbujas salientes."""
        WebDriverWait(self.driver, self.timeout+15+timeout_extra).until(
            lambda d: self._count_outgoing() > before_count
        )

    def _handle_transient_modal(self):
        """Tolera modales transitorios como 'Iniciando chat'; lanza error si es un modal real de fallo."""
        try:
            modal = self.driver.find_element(By.XPATH, "//div[contains(@data-animate-modal-body,'true')]")
            txt = (modal.text or "").strip().lower()
            if any(s in txt for s in ("iniciando chat", "starting chat", "open chat")):
                # esperar textbox o que desaparezca el modal
                try:
                    WebDriverWait(self.driver, self.timeout+15).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='textbox']"))
                    )
                except TimeoutException:
                    WebDriverWait(self.driver, self.timeout+10).until_not(
                        EC.presence_of_element_located((By.XPATH, "//div[contains(@data-animate-modal-body,'true')]"))
                    )
            else:
                # otros modales sí indican error real (número sin cuenta, etc.)
                raise RuntimeError(f"WhatsApp reporta problema: {modal.text.strip()}")
        except NoSuchElementException:
            pass

    def _click_send_or_enter(self):
        """Intenta clic en botón Enviar; si no, ENTER en el textbox."""
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

    def send_text(self, phone_e164: str, body: str) -> str:
        """
        Envío con reintento sin duplicar:
        - Si la confirmación tarda, antes de reintentar verificamos si YA se envió
          (burbujas aumentaron o editor vacío). Solo si no hay señales, reintentamos una vez.
        """
        last_exc = None
        for attempt in range(1, 3):
            try:
                self._ensure_logged_in()

                url = f"https://web.whatsapp.com/send?phone={phone_e164}&text={urllib.parse.quote(body or '')}"
                self.driver.get(url)

                # Espera textbox o modal (margen mayor por lentitud ocasional)
                WebDriverWait(self.driver, self.timeout+20).until(
                    EC.any_of(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div[role='textbox']")),
                        EC.presence_of_element_located((By.XPATH, "//div[contains(@data-animate-modal-body,'true')]"))
                    )
                )

                # Manejo modal transitorio / errores reales
                self._handle_transient_modal()

                # Asegura texto y cuenta antes de enviar
                self._fill_text_if_missing(body or "")
                before = self._count_outgoing()

                # Enviar
                self._click_send_or_enter()

                # Confirmar envío (primer intento)
                try:
                    self._wait_sent_confirmation(before)
                    time.sleep(0.2)
                    return f"selenium-{uuid.uuid4().hex[:12]}"
                except TimeoutException as e:
                    # Antes de reintentar, verifica si YA se envió:
                    already_sent = (self._count_outgoing() > before) or self._is_editor_empty()
                    if already_sent:
                        # Ya se envió, solo tardó la UI en reflejar (evita duplicado)
                        time.sleep(0.2)
                        return f"selenium-{uuid.uuid4().hex[:12]}"
                    # Si no hay señales de envío, dejamos caer al except general para reintentar
                    raise

            except Exception as e:
                last_exc = e
                time.sleep(1.0)  # pequeña espera y reintento

        raise last_exc if last_exc else RuntimeError("Fallo desconocido en send_text()")

    def close(self):
        try:
            self.driver.quit()
        except Exception:
            pass
