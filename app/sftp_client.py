"""
Modul pentru transferul fișierelor EPUB pe Kindle via SSH / SFTP (KOReader SSH Server).
Folosește librăria Paramiko pentru conexiuni securizate și sigure, cu suport pentru
autentificare automată KOReader (parolă goală, parola 'root' sau parolă configurată).
"""

import os
import posixpath
import socket
from typing import Callable, Optional, Tuple

import paramiko


class KindleSFTPClient:
    """
    Client SFTP dedicat pentru încărcarea cărților pe Kindle rulând KOReader.
    KOReader pornește de regulă un server SSH/SFTP pe portul 2222 cu utilizatorul 'root'.
    """

    def __init__(
        self,
        host: str = "192.168.1.50",
        port: int = 2222,
        username: str = "root",
        password: Optional[str] = None,
        remote_dir: str = "/mnt/us/Carti/Biblioteca",
        timeout: int = 8,
    ):
        self.host = host.strip()
        self.port = int(port)
        self.username = username.strip() or "root"
        self.password = password.strip() if password is not None else ""
        self.remote_dir = remote_dir.strip() or "/mnt/us/Carti/Biblioteca"
        self.timeout = timeout

    def _get_ssh_client(self) -> paramiko.SSHClient:
        """Creează și configurează un client Paramiko SSH."""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        return client

    def _connect_ssh(self, client: paramiko.SSHClient):
        """
        Conectare robustă la serverul SSH din KOReader:
        - Dacă este furnizată o parolă, o folosește pe aceea.
        - Dacă parola este lăsată goală, încearcă automat:
          1. Parolă goală ""
          2. Parola implicită 'root'
          3. Chei locale / auth agent
        """
        passwords_to_try = []
        if self.password:
            passwords_to_try.append(self.password)
        else:
            # Pentru KOReader pe Kindle, când parola e goală în UI încercăm ambele variante
            passwords_to_try = ["", "root"]

        last_error = None
        for pw in passwords_to_try:
            try:
                client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.username,
                    password=pw,
                    timeout=self.timeout,
                    allow_agent=True,
                    look_for_keys=True,
                    banner_timeout=10,
                )
                return  # Conexiune reușită!
            except (paramiko.AuthenticationException, paramiko.SSHException) as auth_err:
                last_error = auth_err
                # Dacă mai avem variante de încercat, continuăm
                continue
            except (socket.timeout, TimeoutError) as t_err:
                raise t_err
            except Exception as e:
                raise e

        if last_error:
            raise last_error

    def test_connection(self) -> Tuple[bool, str]:
        """
        Testează dacă adresa IP și portul KOReader sunt accesibile și autentificarea reușește.
        Returnează (succes: bool, mesaj: str).
        """
        if not self.host:
            return False, "Adresa IP a Kindle-ului nu este configurată."

        client = self._get_ssh_client()
        try:
            self._connect_ssh(client)

            # Încercăm deschiderea unui canal SFTP pentru a confirma drepturile de scriere
            sftp = client.open_sftp()
            try:
                # Verificăm / creăm directorul remote
                self._ensure_remote_dir(sftp, self.remote_dir)
            finally:
                sftp.close()

            return True, f"Conexiune SSH/SFTP reușită la {self.host}:{self.port} în {self.remote_dir}!"
        except (socket.timeout, TimeoutError):
            return False, f"Timeout: Kindle-ul nu răspunde la {self.host}:{self.port}. Verificați că Wi-Fi și serverul SSH din KOReader sunt pornite."
        except paramiko.AuthenticationException:
            return False, (
                f"Eroare de autentificare pentru utilizatorul '{self.username}'.\n"
                "Verificați parola afișată pe ecranul Kindle-ului în KOReader (implicit este 'root' sau lăsată goală)."
            )
        except paramiko.SSHException as ssh_err:
            return False, f"Eroare protocol SSH: {str(ssh_err)}"
        except Exception as e:
            return False, f"Eroare conexiune: {str(e)}"
        finally:
            client.close()

    def _ensure_remote_dir(self, sftp: paramiko.SFTPClient, target_dir: str):
        """Recursively ensures the target remote directory exists."""
        parts = [p for p in target_dir.strip("/").split("/") if p]
        current = ""
        for part in parts:
            current = posixpath.join(current, part)
            if not current.startswith("/"):
                current = "/" + current
            try:
                sftp.stat(current)
            except IOError:
                try:
                    sftp.mkdir(current)
                except Exception:
                    pass

    def upload_file(
        self,
        local_file_path: str,
        target_remote_dir: Optional[str] = None,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, str]:
        """
        Uploads any local file (EPUB, PNG, JPG, etc.) to Kindle via SFTP.
        """
        if not os.path.isfile(local_file_path):
            return False, f"Local file does not exist: {local_file_path}"

        effective_dir = (target_remote_dir or self.remote_dir).strip() or "/mnt/us"
        file_name = os.path.basename(local_file_path)
        remote_file_path = posixpath.join(effective_dir, file_name)

        client = self._get_ssh_client()
        try:
            if log_callback:
                log_callback(f"Connecting via SFTP to {self.host}:{self.port}...")

            self._connect_ssh(client)

            sftp = client.open_sftp()
            try:
                self._ensure_remote_dir(sftp, effective_dir)

                if log_callback:
                    log_callback(f"Uploading '{file_name}' to '{remote_file_path}'...")

                file_size = os.path.getsize(local_file_path)

                def sftp_cb(transferred, total):
                    if progress_callback:
                        progress_callback(transferred, file_size)

                sftp.put(local_file_path, remote_file_path, callback=sftp_cb)

                if log_callback:
                    log_callback(f"SFTP upload completed: {file_name}")

                return True, f"File successfully uploaded to Kindle at {remote_file_path}"
            finally:
                sftp.close()

        except Exception as e:
            err = f"SFTP upload failed for '{file_name}': {str(e)}"
            if log_callback:
                log_callback(f"[SSH Error] {err}")
            return False, err
        finally:
            client.close()

    # Alias for backwards compatibility
    upload_epub = upload_file
