# Cámara LAN

Versión actual: **1.0.1**. Consulta el historial en [CHANGELOG.md](CHANGELOG.md).

Usa dos equipos conectados a la misma red local. **Compartir** se abre en el equipo Windows 11 que tiene la cámara. **Ver** se abre en el otro equipo.

## Preparar los dos equipos

1. Extrae la carpeta del proyecto en ambos equipos.
2. Usa una instalación estándar de Python 3.12 a 3.15 para Windows x64 con Tcl/Tk. Si ya tienes Python 3.15, puedes usarlo.
3. Ejecuta **Preparar.cmd** una vez en cada equipo. Este paso descarga las dependencias. Si ya habías preparado la aplicación, vuelve a ejecutarlo en ambos equipos al instalar esta versión. La transmisión posterior se realiza dentro de la LAN.

Si **Preparar.cmd** indica que Tkinter o Tcl/Tk no se pudo cargar, ejecuta `py -3 -m tkinter` en una consola. Una ventana de demostración confirma que funciona. Si usas Python sin el lanzador `py`, prueba `python -m tkinter`.

## Compartir y ver

1. En el equipo con cámara, ejecuta **Compartir.cmd**. Indica su IPv4 local, que puedes ver con `ipconfig`, y el puerto. El valor inicial es `8443`.
2. Pulsa **Generar código** para obtener un código privado de exactamente **8 dígitos numéricos** y comunícalo únicamente a la persona que verá la cámara. Conserva los ceros iniciales si aparecen. Puedes dejar el programa esperando en el icono junto al reloj. Si Windows pregunta por el firewall, permite el acceso solo en la red privada que estás usando.
3. En el otro equipo, ejecuta **Ver.cmd**, escribe la IPv4 y el puerto del anfitrión y escribe o pega el código de 8 dígitos. Solicita la conexión. Ambos equipos deben usar esta versión de la aplicación.
4. La persona que está delante de la cámara comprueba la solicitud y pulsa **Aceptar y encender cámara**. Mientras comparte, tiene una vista previa y un botón para detener la sesión.
5. Si el anfitrión habilitó los controles del visor, este puede pausar, reanudar y ajustar la calidad y la frecuencia de imagen dentro de los límites locales. Cualquiera de los dos equipos puede finalizar la sesión.

El video se envía cifrado a un visor a la vez. Cada sesión dura hasta 30 minutos. Bloquear Windows o esconder la vista de transmisión cierra la sesión. El video se mantiene en memoria durante la transmisión y el visor recibe únicamente imagen en vivo.

Se permiten hasta 5 intentos de conexión por minuto. Si alcanzas ese límite, espera un minuto antes de volver a solicitar la conexión.

Si el equipo con cámara no la detecta, revisa **Configuración → Privacidad y seguridad → Cámara** y permite el acceso a las aplicaciones de escritorio. Si los equipos están en una red de invitados, es posible que esa red impida que se comuniquen entre sí.

## Generar un ejecutable en Windows

Después de preparar las dependencias, ejecuta **Compilar.cmd** en Windows 11 x64. El resultado queda en `dist\CamaraLAN.exe`. Al abrirlo puedes elegir **Compartir** o **Ver y controlar**.
