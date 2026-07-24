import wave
import struct
from pathlib import Path
import tkinter as tk
from clip_editor import ClipEditorDialog

path = Path('c:/MixCreatorPro/test_clip.wav')
with wave.open(str(path), 'w') as f:
    f.setparams((1, 2, 44100, 44100, 'NONE', 'not compressed'))
    f.writeframes(struct.pack('<h', 0) * 44100)

print('START DIALOG')
root = tk.Tk()
root.withdraw()
try:
    dlg = ClipEditorDialog(root, path, None, lambda x: None)
    print('DIALOG CREATED', dlg)
    root.after(2000, root.destroy)
    root.mainloop()
    print('EVENT LOOP EXITED')
except Exception as e:
    import traceback
    traceback.print_exc()
    raise
