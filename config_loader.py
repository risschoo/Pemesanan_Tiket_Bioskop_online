"""
config_loader.py
Membaca konfigurasi dari file config.env secara aman.
Import modul ini di app.py untuk menghindari hardcode credentials.
"""
import os

def load_env(filepath='config.env'):
    """Baca file .env sederhana (key=value) dan masukkan ke os.environ."""
    if not os.path.exists(filepath):
        print(f"[WARNING] File konfigurasi '{filepath}' tidak ditemukan. Menggunakan environment variables yang sudah ada.")
        return
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, _, val = line.partition('=')
                # Hanya set jika belum ada di environment (environment OS lebih prioritas)
                if key.strip() not in os.environ:
                    os.environ[key.strip()] = val.strip()

def get(key, default=None):
    return os.environ.get(key, default)

# Auto-load saat module ini di-import
load_env()
