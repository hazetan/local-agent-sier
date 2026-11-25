import os
import shlex
import socket
import subprocess

from .config import (
    get_nas_enable,
    get_nas_password,
    get_nas_port,
    get_nas_user,
    get_samba_enable,
    get_samba_port,
    get_workspace_dir,
)

SAMBA_CONTAINER_NAME = "sier_workspace_samba"


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def get_nas_status_summary() -> str:
    if not get_nas_enable():
        return "NAS: Disabled"
    ip = get_local_ip()
    port = get_nas_port()
    smb = f" | SMB:\\\\{ip}\\workspace" if get_samba_enable() else ""
    return f"NAS: http://{ip}:{port} (Web/DAV){smb}"


def ensure_samba_service() -> None:
    """SAMBA_ENABLE=True の場合、workspace/ を共有する軽量Sambaコンテナを起動する"""
    subprocess.run(["docker", "rm", "-f", SAMBA_CONTAINER_NAME], check=False, capture_output=True)
    if not get_samba_enable():
        return

    ws_dir = get_workspace_dir().resolve()
    user = get_nas_user() or "agent"
    password = get_nas_password()
    smb_port = get_samba_port()

    if not password:
        print("[NAS] Warning: NAS_PASSWORD is not set in .env. Skipping Samba startup for security.")
        return

    subprocess.run(["sudo", "-n", "systemctl", "stop", "smbd", "nmbd"], check=False, capture_output=True)

    uid = os.getuid()
    gid = os.getgid()

    smb_conf = (
        "[global]\n"
        "workgroup = WORKGROUP\n"
        "server string = SIer Workspace NAS\n"
        "security = user\n"
        "map to guest = Never\n"
        "ntlm auth = yes\n"
        "server min protocol = SMB2\n"
        "create mask = 0664\n"
        "directory mask = 0775\n"
        f"force user = {user}\n"
        "force group = nasgroup\n\n"
        "[workspace]\n"
        "path = /workspace\n"
        "browsable = yes\n"
        "writable = yes\n"
        "read only = no\n"
        f"valid users = {user}\n"
    )

    q_user = shlex.quote(user)
    q_pass = shlex.quote(password)
    q_conf = shlex.quote(smb_conf)

    setup_script = (
        "apk add --no-cache samba >/dev/null && "
        "mkdir -p /var/lib/samba/private /run/samba && "
        f"addgroup -g {gid} nasgroup 2>/dev/null || true && "
        f"adduser -D -H -u {uid} -G nasgroup {q_user} 2>/dev/null || true && "
        f"printf '%s\\n%s\\n' {q_pass} {q_pass} | smbpasswd -a -s {q_user} && "
        f"smbpasswd -e {q_user} && "
        f"printf '%s' {q_conf} > /etc/samba/smb.conf && "
        "smbd --foreground --no-process-group"
    )

    cmd = [
        "docker", "run", "-d",
        "--name", SAMBA_CONTAINER_NAME,
        "-p", f"{smb_port}:445",
        "-v", f"{ws_dir}:/workspace",
        "alpine:latest",
        "sh", "-c", setup_script,
    ]
    res = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if res.returncode == 0:
        print(f"[NAS] Samba server started: \\\\{get_local_ip()}\\workspace (user: {user})")
    else:
        subprocess.run(["docker", "rm", "-f", SAMBA_CONTAINER_NAME], check=False, capture_output=True)
        print(f"[NAS] Failed to start Samba container: {res.stderr.strip()}")


def stop_samba_service() -> None:
    subprocess.run(["docker", "rm", "-f", SAMBA_CONTAINER_NAME], check=False, capture_output=True)