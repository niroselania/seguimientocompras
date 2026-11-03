# Seguimiento Descuento

App web para registrar las mismas columnas de la planilla original y controlar el cupo de compra.

## Regla de cupo

- Cupo por persona: $1.400.000.
- El periodo inicial empieza el 01/05/2026.
- Cada periodo dura 6 meses.
- Si el total de una persona dentro del periodo supera el cupo, la app muestra una advertencia.

## Columnas

- Año Fiscal
- Fecha de Compra
- Nombre y Apellido
- DNI
- Mail asociado a Patagonia
- Numero de Orden
- Monto de la compra

## Portainer

1. Subir esta carpeta como stack o repositorio.
2. Usar el `docker-compose.yml`.
3. Publicar el puerto `7070`.
4. Mantener el volumen `seguimiento_descuento_data` para conservar la base SQLite.

La primera vez que inicia, carga las filas actuales de `seed_data.json` si la base esta vacia.

## Backup

El botón `Exportar Excel` permite elegir un semestre y una persona, o exportar todos los períodos y empleados.

## Bot de búsqueda de Telegram

El bot permite escribir un nombre y consultar el total gastado en cada período semestral. Al escribir `todos` o enviar `/todos`, lista a todas las personas con compras, separadas por nombre, con sus totales de cada semestre. Si el listado es largo, lo envía en varios mensajes. Para activarlo, configura estas variables de entorno en el stack de Portainer:

- `TELEGRAM_BOT_TOKEN`: token que entrega BotFather.
- `TELEGRAM_ALLOWED_CHAT_IDS`: ID de Telegram autorizado; se pueden indicar varios separados por comas.

Puedes consultar tu ID hablando con `@userinfobot`. Configura solo los chats privados que deban ver los datos. No publiques el token en el repositorio ni lo compartas en el chat. Al reiniciar el stack, el servidor inicia la consulta del bot. Si falta alguna variable, el bot queda desactivado y el sitio web sigue funcionando.

En Telegram, envía `/start` y luego el nombre de una persona. Si hay varias coincidencias, el bot devuelve opciones para afinar la búsqueda. Para recibir mensajes, el servidor necesita acceso saliente a `api.telegram.org`. Esta integración usa `getUpdates`; Telegram no permite usarlo simultáneamente con un webhook o con otro consumidor de actualizaciones para el mismo bot.
