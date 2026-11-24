# Cámara LAN

Versión actual: **1.1.2**. Consulta el historial en [CHANGELOG.md](CHANGELOG.md).

Usa dos equipos conectados a la misma red local. **Compartir** se abre en el equipo Windows 11 que tiene la cámara. **Ver** se abre en el otro equipo.

## Preparar los dos equipos

1. Extrae la carpeta del proyecto en ambos equipos.
2. Usa una instalación estándar de Python 3.12 a 3.15 para Windows x64 con Tcl/Tk. Si ya tienes Python 3.15, puedes usarlo.
3. Ejecuta **Preparar.cmd** una vez en cada equipo. Este paso descarga las dependencias. Si ya habías preparado la aplicación, vuelve a ejecutarlo en ambos equipos al instalar esta versión. La transmisión posterior se realiza dentro de la LAN.

Si **Preparar.cmd** indica que Tkinter o Tcl/Tk no se pudo cargar, ejecuta `py -3 -m tkinter` en una consola. Una ventana de demostración confirma que funciona. Si usas Python sin el lanzador `py`, prueba `python -m tkinter`.

## Compartir y ver

1. En el equipo con cámara, ejecuta **Compartir.cmd**. Indica su IPv4 local, que puedes ver con `ipconfig`, y el puerto. El valor inicial es `8443`.
2. Pulsa **Habilitar conexión** para abrir la vista previa local y esperar solicitudes. Si Windows pregunta por el firewall, permite el acceso en la red privada que estás usando.
3. En el otro equipo, ejecuta **Ver.cmd**, busca las cámaras de la LAN y selecciona la que quieres ver. Si no aparece, escribe la IPv4 y el puerto del anfitrión. Pulsa **Solicitar conexión**. Usa la versión **1.1.0 o posterior** en ambos equipos.
4. La persona que está delante de la cámara comprueba la solicitud y pulsa **Aceptar y compartir** para enviar el video al visor.
5. Si el anfitrión habilitó los controles del visor, este puede pausar, reanudar y ajustar la calidad y la frecuencia de imagen dentro de los límites locales. Cualquiera de los dos equipos puede finalizar la sesión.

En el anfitrión, pulsa **Cerrar conexión** en la ventana o en el menú del icono junto al reloj para dejar de anunciar la cámara, liberar el puerto y apagar la cámara si estaba activa. Puedes volver a habilitar la conexión en el mismo puerto.

**Minimizar a la bandeja** quita la ventana de la barra de tareas y deja el icono junto al reloj. También puedes usar el botón de minimizar de Windows. Una solicitud nueva se indica en el icono y espera a que elijas **Abrir** para responder.

Cámara LAN admite una sola instancia por sesión de Windows. Para cambiar de modo o actualizar, cierra la instancia anterior. En **Compartir**, elige **Salir** en el menú del icono junto al reloj.

El video se envía cifrado a un visor a la vez. Cada sesión dura hasta 30 minutos. Bloquear Windows u ocultar la ventana apaga la cámara. Si estabas esperando una solicitud, puedes pulsar **Reanudar cámara** al regresar. Si estabas compartiendo, se cierra la sesión. El visor recibe únicamente imagen en vivo.

Acepta únicamente solicitudes del equipo que esperas. En la primera conexión aprobada, el visor guarda la identidad de la cámara para esa IP y puerto. Si la identidad cambia, el visor avisa y bloquea la conexión. Si confirmas que se trata de un cambio esperado, pulsa **Olvidar identidad** en el visor y solicita una nueva conexión.

Se permiten hasta 5 intentos de conexión por minuto. Si alcanzas ese límite, espera un minuto antes de volver a solicitar la conexión.

Si el equipo con cámara no la detecta, revisa **Configuración → Privacidad y seguridad → Cámara** y permite el acceso a las aplicaciones de escritorio. La búsqueda usa multicast IPv4 por UDP `55443`. Si la red bloquea multicast, usa la IPv4 y el puerto del anfitrión. Las redes de invitados pueden impedir la comunicación entre equipos.

## Generar un ejecutable en Windows

Después de preparar las dependencias, ejecuta **Compilar.cmd** en Windows 11 x64. El resultado queda en `dist\CamaraLAN.exe`. Al abrirlo puedes elegir **Compartir** o **Ver y controlar**.
