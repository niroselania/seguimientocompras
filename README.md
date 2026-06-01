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
3. Publicar el puerto `8080`.
4. Mantener el volumen `seguimiento_descuento_data` para conservar la base SQLite.

La primera vez que inicia, carga las filas actuales de `seed_data.json` si la base esta vacia.
