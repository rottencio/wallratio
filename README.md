# Adaptador de wallpapers

Herramienta para adaptar imágenes a una relación de aspecto elegida sin ampliar,
reducir ni recortar los píxeles originales. El lienzo se expande solo en el eje
necesario para alcanzar lo más posible la proporción; como los píxeles son
enteros, puede haber una diferencia inferior a un píxel en ese eje. El tamaño
final varía entre imágenes y puede ser mayor que la resolución de origen.

## Requisitos

- Python 3.10 o posterior
- Pillow (se instala con los pasos siguientes)
- Tkinter para la interfaz gráfica (incluido normalmente en Python para Windows)

## Instalación en Windows

Desde la carpeta del proyecto, abre PowerShell y ejecuta:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Si PowerShell no permite activar el entorno, puedes ejecutar los comandos con
`.\.venv\Scripts\python.exe` sin activarlo.

## Interfaz gráfica

```powershell
python .\wallpaper_tool.py
```

Elige la carpeta de entrada, la carpeta de salida, la proporción y el formato.
También se puede escribir una proporción personalizada, por ejemplo `5:4`.

## Línea de comandos

```powershell
python .\wallpaper_tool.py .\wallpapersEjemplo --ratio 9:16
python .\wallpaper_tool.py .\wallpapersEjemplo --ratio 16:9 --format original
python .\wallpaper_tool.py .\wallpapersEjemplo --ratio 21:9 --format webp --output .\salida_21_9
python .\wallpaper_tool.py --help
```

Formatos disponibles: `png` (predeterminado), `jpeg`, `webp` y `original`.
Al convertir, se conserva el nombre base y se usa la extensión correspondiente.
Si se indica `--output`, esa carpeta debe ser nueva; no se sobrescribe ninguna
salida existente. Si no se indica, se crea una carpeta nueva junto a la entrada.

## Tratamiento de imágenes

- Se conservan todos los píxeles originales; las imágenes no se escalan ni se recortan.
- Si el borde que se necesita extender es de color casi uniforme, se prolonga
  ese color para que el dibujo y su fondo liso se integren sin una costura visible.
- En imágenes con bordes complejos se genera un fondo suavizado a partir de la
  imagen y se superpone encima la imagen original intacta.
- Las imágenes con transparencia conservan el canal alfa en PNG; JPEG no admite
  transparencia y la compone sobre blanco.
- Se procesa de forma recursiva y se conserva la estructura de subcarpetas.
  Los GIF animados y otros archivos animados se notifican como errores en lugar
  de descartar fotogramas silenciosamente.
- Se mantiene el perfil ICC cuando está disponible. Las imágenes que excedan
  límites de tamaño seguros y las colisiones de nombres se notifican.
