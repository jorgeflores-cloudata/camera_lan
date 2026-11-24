# Cambios

## 1.1.2 — 2026-11-24

- **Minimizar a la bandeja** y el botón de minimizar de Windows dejan solo el icono junto al reloj. El menú del icono permite volver a abrir la ventana.
- Una solicitud recibida mientras la ventana está oculta se mantiene pendiente sin abrir la ventana automáticamente.

## 1.1.1 — 2026-11-24

- **Habilitar conexión** abre la vista previa local. El visor recibe video después de que el anfitrión pulse **Aceptar y compartir**.
- Es posible pausar la cámara mientras se esperan solicitudes. Al ocultar la ventana o bloquear Windows, la vista previa se apaga y requiere reanudación local.

## 1.1.0 — 2026-11-24

- El visor descubre cámaras en la LAN y admite conexión manual por IPv4 y puerto. Cada sesión requiere aprobación en el anfitrión y el video se transmite por TLS.
- El visor recuerda la identidad de la cámara después de la primera conexión aprobada y bloquea cambios inesperados.
- Cámara LAN permite una sola instancia por sesión de Windows, incluso si se abre desde otra carpeta o en otro modo.
- **Cerrar conexión** está disponible en la ventana de Compartir y en el icono junto al reloj. Libera el puerto configurado y apaga la cámara si estaba activa.
- Si el puerto está ocupado, Cámara LAN muestra el número de puerto y cómo resolver el conflicto.

Actualiza ambos equipos para conectarte sin código.

## 1.0.1 — 2026-11-24

- Código privado aleatorio de exactamente ocho dígitos, incluidos ceros iniciales.
- Campo de código más legible al compartir y entrada numérica en el visor.
- Emparejamiento con SPAKE2 vinculado al certificado TLS, sin enviar el código privado.
- Límite de cinco intentos de conexión por minuto; se conserva la aprobación local de cada sesión.
- Pruebas de conexión cifrada, validación, rechazo y límite de intentos sin activar la cámara.

Ambos equipos deben actualizarse y volver a ejecutar `Preparar.cmd`.
