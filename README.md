# wallratio

Versión 0.2

Licencia: GNU General Public License, versión 3. Consulta [LICENSE](LICENSE).

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

## Crear un ejecutable único (.exe)

Instala los requisitos de compilación y genera el ejecutable desde PowerShell:

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --clean --noconfirm --onefile --name wallratio --icon ..\assets\wallratio.ico --add-data "..\assets\wallratio.ico;assets" --specpath build .\wallratio.py
```

El archivo resultante estará en `.\dist\wallratio.exe`. Incluye Python,
Pillow y Tkinter; no requiere una instalación de Python en el equipo de destino.
El icono se aplica tanto al ejecutable como a la ventana gráfica. El ejecutable
conserva la consola para que también funcionen la CLI y `--help`; al iniciarlo
sin argumentos abre la interfaz gráfica.

## Interfaz gráfica

```powershell
python .\wallratio.py
```

Elige la carpeta de entrada, la carpeta de salida, la proporción y el formato.
También se puede escribir una proporción personalizada, por ejemplo `5:4`.

## Línea de comandos

```powershell
python .\wallratio.py .\wallpapersEjemplo --ratio 9:16
python .\wallratio.py .\wallpapersEjemplo --ratio 16:9 --format original
python .\wallratio.py .\wallpapersEjemplo --ratio 21:9 --format webp --output .\salida_21_9
python .\wallratio.py --help
```

Formatos disponibles: `png` (predeterminado), `jpeg`, `webp` y `original`.
Al convertir, se conserva el nombre base y se usa la extensión correspondiente.
Si dos imágenes con el mismo nombre base colisionan al convertir, se añade al
nombre el formato de origen (por ejemplo, `wallpaper_jpg.png` y
`wallpaper_png.png`).
Si se indica `--output`, esa carpeta debe ser nueva; no se sobrescribe ninguna
salida existente. Si no se indica, se crea una carpeta nueva junto a la entrada.

## Tratamiento de imágenes

- Se conservan todos los píxeles originales; las imágenes no se escalan ni se recortan.
- Si los bordes opuestos que se necesitan extender coinciden en color, se
  prolonga su perfil completo. Así también se conservan fondos lisos con un
  gradiente suave, como `49375.jpg`, sin difuminarlos.
- Si la imagen tiene un fondo predominantemente negro, se amplía con negro; los
  detalles no negros que tocan el borde se funden en una franja muy estrecha.
- En imágenes con bordes complejos se usa un color mate tomado del borde y se
  difumina solo una franja estrecha junto a la imagen. Se evita extender una
  versión ampliada y borrosa de toda la imagen, que podía crear bandas anchas.
  La imagen original se superpone intacta.
- Las imágenes con transparencia conservan el canal alfa en PNG; JPEG no admite
  transparencia y la compone sobre blanco.
- Se procesa de forma recursiva y se conserva la estructura de subcarpetas.
  Los GIF animados y otros archivos animados se notifican como errores en lugar
  de descartar fotogramas silenciosamente.
- Se mantiene el perfil ICC cuando está disponible. Las imágenes que excedan
  límites de tamaño seguros y las colisiones de nombres se notifican.
