"""Bundle the x64 Windows default PortAudio library used by this application."""
from pathlib import Path

from PyInstaller.utils.hooks import get_module_file_attribute

audio_dir = Path(get_module_file_attribute("sounddevice")).parent / "_sounddevice_data" / "portaudio-binaries"
binaries = [(str(audio_dir / "libportaudio64bit.dll"), "_sounddevice_data/portaudio-binaries")]
datas = [(str(audio_dir / "README.md"), "_sounddevice_data/portaudio-binaries")]
