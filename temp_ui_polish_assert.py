import time
import tkinter as tk
from pathlib import Path
from clip_editor import ClipEditorDialog

out_path = Path('c:/MixCreatorPro/temp_ui_polish_assert.txt')

def pump(root, seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        root.update_idletasks()
        root.update()
        time.sleep(0.02)

root = tk.Tk()
root.withdraw()
dlg = None
lines = []
try:
    dlg = ClipEditorDialog(root, Path('c:/MixCreatorPro/test_clip.mp3'), None, lambda _clip: None)
    pump(root, 1.0)
    lines.append(f'geometry={dlg.winfo_width()}x{dlg.winfo_height()}')
    lines.append(f'zoom_label={dlg.zoom_level_label.cget("text") if dlg.zoom_level_label else "n/a"}')

    dlg._play()
    pump(root, 0.6)
    lines.append(f'first_play={dlg.player.is_playing()} state={dlg.play_state}')

    dlg._on_waveform_seek(300)
    pump(root, 0.3)
    lines.append(f'seek_current={dlg.current_ms}')

    if dlg.waveform_widget is not None:
        z0 = dlg.waveform_widget.get_zoom()
        dlg._zoom_in_ui(); pump(root, 0.2)
        z1 = dlg.waveform_widget.get_zoom()
        dlg._zoom_out_ui(); pump(root, 0.2)
        z2 = dlg.waveform_widget.get_zoom()
        dlg._zoom_fit_ui(); pump(root, 0.2)
        z3 = dlg.waveform_widget.get_zoom()
        lines.append(f'zoom_seq={z0},{z1},{z2},{z3}')
finally:
    try:
        if dlg is not None and dlg.winfo_exists():
            dlg.destroy()
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass

out_path.write_text('\n'.join(lines), encoding='utf-8')
