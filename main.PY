# -*- coding: utf-8 -*-
"""
MixCreator PRO
File: main.py
Versione: 1.0

Punto di avvio dell'applicazione.
"""

from gui import MixCreatorApp


def main() -> None:
    app = MixCreatorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
