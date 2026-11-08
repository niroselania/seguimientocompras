# Seguimiento de compras · Patagonia

Sitio interno para registrar compras y controlar el cupo semestral por persona. La interfaz usa un panel oscuro con navegación lateral, tarjetas de resumen y una tabla compacta.

En la barra superior, cada persona puede alternar entre tema claro y oscuro. La selección queda guardada en el navegador que utiliza.

## Regla del cupo

- Cupo por persona: $1.400.000.
- El primer período comienza el 01/05/2026 y se renueva cada seis meses.
- Las compras nuevas reciben la fecha del día; al editar una orden se puede ajustar su fecha.
- Los registros anteriores se conservan en la base de datos.

## Exportación

El selector permite descargar compras de un período semestral y de una persona, o exportar todos los períodos y empleados. El archivo incluye el total y el saldo por persona en cada período.

## Bot de Telegram

Para activarlo en Portainer, configura `TELEGRAM_BOT_TOKEN` y `TELEGRAM_ALLOWED_CHAT_IDS` como variables de entorno del stack. La segunda variable contiene uno o más IDs de chat separados por comas. El bot responde a un nombre con sus totales por semestre; si se escribe `todos` o `/todos`, lista todas las personas con compras, separadas por nombre y DNI. Configura solo chats privados autorizados y guarda el token como secreto.

Esta integración usa consulta periódica. No puede funcionar al mismo tiempo que un webhook ni con otro consumidor de actualizaciones del mismo bot.

## Portainer

1. Subir esta carpeta como stack o repositorio.
2. Usar `docker-compose.yml` y publicar el puerto `7070`.
3. Mantener el volumen `seguimiento_descuento_data` para conservar la base SQLite.
4. Al cambiar código, reconstruir y volver a desplegar el contenedor.

La primera vez que inicia, carga `seed_data.json` si la base está vacía.
