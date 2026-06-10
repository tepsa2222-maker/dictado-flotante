# Dictado Flotante v2

Herramienta de transcripción de voz a texto en tiempo real para GNOME/Wayland.
Graba tu voz, transcribe con Groq Whisper large-v3 y guarda el texto automáticamente.

## Características

- Transcripción en español en menos de 2 segundos
- Ventana flotante siempre visible en GNOME
- Auto-guardado en ~/Dictados/
- Copiado al portapapeles con wl-copy (Wayland nativo)
- Historial de los últimos 20 dictados
- Costo: $0 (plan gratuito de Groq)

## Requisitos

- Fedora / GNOME / Wayland
- Python 3.12+
- GTK4 + Adwaita
- Cuenta gratuita en groq.com para obtener API key

## Instalación

pip install groq pyaudio

mkdir -p ~/.config/dictadoflov1
echo "GROQ_API_KEY=tu_api_key_aqui" > ~/.config/dictadoflov1/config.env

python3 dictadofloV2.py

## Stack

Python · GTK4 · Adwaita · Groq API · Whisper large-v3 · PyAudio · Wayland
