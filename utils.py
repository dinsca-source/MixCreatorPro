from __future__ import annotations

from pathlib import Path


def scan_mp3_files(folder_path: str | Path) -> list[Path]:
	"""
	Restituisce i file MP3 presenti solo nel primo livello della cartella.
	Non attraversa mai sottocartelle.
	"""
	folder = Path(folder_path).expanduser()

	if not folder.is_dir():
		raise FileNotFoundError(f"Cartella non valida: {folder}")

	files: list[Path] = []
	for item in folder.iterdir():
		if not item.is_file():
			continue
		if item.suffix.lower() != ".mp3":
			continue
		files.append(item)

	files.sort(key=lambda path: path.name.lower())
	return files

