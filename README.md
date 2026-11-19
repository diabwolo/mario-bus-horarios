# Servidor de horarios de Mario-Bus

Servidor automático (GitHub Actions + GitHub Pages) que mantiene al día los horarios de **Mario-Bus** a partir del GTFS oficial del
Consorcio Regional de Transportes de Madrid (CRTM). Funciona sin el ordenador de Mario.

> Datos: © CRTM — «Powered by CRTM» · https://www.crtm.es (datos elaborados a partir del GTFS del CRTM; app no oficial).
> Licencia de uso: https://crtm.es/licencia-de-uso

## Qué hace (una vez al día)
1. Pide a la fuente oficial un resumen de ≈1 KB (fecha de modificación y tamaño). **Si no cambió, termina:** no descarga, no convierte, no publica.
2. Si cambió: descarga el GTFS (76 MB) con comprobación de tamaño y ZIP.
3. Lo valida: calendarios, fechas, referencias, horas, paradas, **servicio efectivo por línea y día** y lo compara con la última versión buena
   (`estado/base_resumen.json`): si falta una línea, un día de servicio o caen las expediciones más de un 50 %, **se rechaza**.
4. Genera `moncloa_islas.json`, `salidas.json`, `horarios.json`, `paradas.json`, escribe `manifest.json` y lo **firma** (`manifest.sig`, ECDSA P-256).
5. Solo si todo es correcto y completo, publica en GitHub Pages. **Nunca se publican paquetes parciales** ni existe la aprobación manual de parciales.
6. Si algo falla: no se publica nada y Pages sigue sirviendo la última versión válida. El trabajo queda en rojo y GitHub avisa por correo.

## Qué se publica (en la dirección de Pages)
`manifest.json` · `manifest.sig` · `salidas.json` · `horarios.json` · `moncloa_islas.json` · `paradas.json`

## Dónde está cada cosa
| Ruta | Qué es |
|---|---|
| `.github/workflows/horarios.yml` | El programa diario (y las acciones manuales) |
| `tools/` | Los scripts (el mismo código que prueba la app de Mario) y sus pruebas |
| `estado/publicable/` | Lo que está publicado ahora (es lo que sirve Pages) |
| `estado/historial/<versión>/` | Versiones anteriores (para restaurar) |
| `estado/estado.json`, `estado/base_resumen.json` | Qué fuente se procesó y la «última versión buena» |
| `prueba_controlada/` | Los 4 JSON reales que ya lleva la app, solo para la acción «prueba_controlada» |
| `tools/regresiones_explicadas.json` | Líneas que el Consorcio suprima a propósito (con su motivo) para que no bloqueen |

## Acciones manuales (Actions → «Horarios Mario-Bus» → Run workflow)
- **ciclo**: lo de cada día.  **ciclo_forzado**: ignora el «una vez al día» y el «sin cambios» (vuelve a descargar y validar).
- **prueba_controlada**: publica, **marcada como prueba y firmada**, una actualización con los mismos datos reales que ya lleva la app, para
  demostrar el recorrido servidor → móvil. No es un feed nuevo del Consorcio. Solo la acepta la variante de prueba de la app.
- **restaurar** + versión: vuelve a publicar una versión de `estado/historial/` (se refirma con secuencia nueva).

## Seguridad
- La clave privada de firma **solo** está en *Settings → Secrets and variables → Actions → `HORARIOS_FIRMA_PRIVADA`*. El workflow la escribe en
  el disco de la máquina efímera, la usa y la borra. No aparece en el código ni en archivos públicos. La app solo lleva la clave pública.
- La app verifica la firma y las huellas SHA-256 antes de activar nada, y rechaza manifiestos con secuencia menor (copias viejas).
- Si la clave se perdiera o se filtrara: generar otra (`python tools/firma_manifiesto.py --generar-claves`), cambiar el secreto y sacar una versión
  de la app con la nueva clave pública (la app admite una lista de claves para el relevo).

## Mantenimiento
- Mirar de vez en cuando la pestaña **Actions**: cada día debe haber una ejecución. «SIN_CAMBIOS» o «OMITIDO» es lo normal.
- Si una ejecución sale en rojo por «Servicio que había y ya no está»: es que el Consorcio publicó un feed incompleto. No hay que hacer nada;
  cuando publique otro completo se procesará solo. Si una línea se suprime de verdad, añadirla (con motivo) a `tools/regresiones_explicadas.json`.
- GitHub desactiva los programas de repositorios sin actividad; aquí el propio trabajo escribe `estado/ultimo_latido.txt` cada día para evitarlo.
