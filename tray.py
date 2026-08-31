import os, subprocess, sys
import pystray
from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
proc = None

def start():
    global proc
    if proc is None or proc.poll() is not None:
        if os.name == 'nt':
            cmd = os.path.join(BASE, 'start.bat')
            proc = subprocess.Popen(['cmd.exe', '/c', cmd], cwd=BASE, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            proc = subprocess.Popen([os.path.join(BASE, 'start.sh')], cwd=BASE)

def stop(icon=None, item=None):
    global proc
    if proc and proc.poll() is None:
        proc.terminate()
    if icon:
        icon.stop()

def restart(icon=None, item=None):
    stop()
    start()

def icon_image():
    im = Image.new('RGBA', (64, 64), (32, 34, 38, 255))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((8, 8, 56, 56), radius=14, outline=(160, 165, 175, 255), width=3)
    d.ellipse((21, 23, 27, 29), fill=(220, 220, 225, 255))
    d.ellipse((37, 23, 43, 29), fill=(220, 220, 225, 255))
    d.arc((20, 24, 44, 43), 10, 170, fill=(180, 185, 195, 255), width=3)
    return im

if __name__ == '__main__':
    start()
    pystray.Icon(
        'feedback_bot', icon_image(), 'Feedback Bot',
        pystray.Menu(
            pystray.MenuItem('Перезапустить', restart),
            pystray.MenuItem('Остановить', stop),
        ),
    ).run()
