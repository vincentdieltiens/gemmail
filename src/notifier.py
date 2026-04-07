import subprocess
import os


def send_notification(title: str, body: str, urgency: str = "normal"):
    """
    Envoie une notification desktop via notify-send.
    Fonctionne depuis Docker grâce au socket D-Bus monté en volume.
    urgency: low | normal | critical
    """
    env = os.environ.copy()
    # D-Bus est monté via le volume /run/user/1000/bus
    if "DBUS_SESSION_BUS_ADDRESS" not in env:
        env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/run/user/1000/bus"

    try:
        subprocess.run(
            [
                "notify-send",
                f"--urgency={urgency}",
                "--app-name=GemMail",
                "--icon=mail-unread",
                title,
                body,
            ],
            env=env,
            check=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        # Fallback : log console si notify-send échoue
        print(f"[NOTIF] {title}: {body} (erreur desktop: {e})")


def notify_important_email(sender: str, subject: str, resume: str):
    send_notification(
        title=f"📧 Email important : {subject[:50]}",
        body=f"De : {sender}\n{resume}",
        urgency="critical",
    )


def notify_daily_summary(summary: str):
    # Les notifications ont une limite de caractères, on tronque
    send_notification(
        title="📬 Résumé email journalier",
        body=summary[:300],
        urgency="normal",
    )
