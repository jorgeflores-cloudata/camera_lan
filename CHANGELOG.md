# Cambios

## 1.0.1 — 2026-11-24

- Código privado aleatorio de exactamente ocho dígitos, incluidos ceros iniciales.
- Campo de código más legible al compartir y entrada numérica en el visor.
- Emparejamiento con SPAKE2 vinculado al certificado TLS, sin enviar el código privado.
- Límite de cinco intentos de conexión por minuto; se conserva la aprobación local de cada sesión.
- Pruebas de conexión cifrada, validación, rechazo y límite de intentos sin activar la cámara.

Ambos equipos deben actualizarse y volver a ejecutar `Preparar.cmd`.
