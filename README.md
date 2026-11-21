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

## Quién puede pedir qué
- **Cron de GitHub y llamadas por API (incluido el respaldo externo de cron-job.org, `origen=respaldo-externo`)**: SOLO el **ciclo normal**
  (mirar la fuente, descargar si cambió y publicar solo si es completo). La pantalla «Run workflow» ya no ofrece acciones ni versión, y Python
  lo vuelve a comprobar con el evento real que pone GitHub (variable `MARIOBUS_EVENTO`): quien llama a la API no puede falsearlo.
- **Acciones extraordinarias** — solo con un `git push` de `disparar/accion.txt` (línea 1; línea 2 = versión para restaurar), que exige poder
  escribir en el repositorio:
  - **ciclo_forzado**: ignora el «una vez al día» y el «sin cambios» (vuelve a descargar y validar).
  - **prueba_controlada**: publica, **marcada como prueba y firmada**, una actualización con los mismos datos reales que ya lleva la app. No es
    un feed nuevo del Consorcio y solo la acepta la variante de prueba de la app. **Nunca sustituye a una publicación oficial** (el código se
    niega); solo puede ir sobre otra prueba o sobre nada.
  - **restaurar** + versión: vuelve a publicar una versión de `estado/historial/` (se refirma con secuencia nueva).

## Protecciones de la ejecución (`tools/guardias_ejecucion.py`)
- **Vigencia (primer paso, antes de usar la clave):** una ejecución solo trabaja si sigue siendo válida respecto a `main`. Una **reejecución**
  (intento > 1) se aborta siempre: usaría el commit, el código y el estado de cuando se creó. Si `main` avanzó solo por commits de estado o de
  código (los de una ejecución anterior en cola), la ejecución se **pone al día y sigue**; se aborta si cambió el workflow, si cambió
  `disparar/` (en un push) o si el commit ya no es antepasado de `main`. Un aborto no usa el secreto, no descarga, no escribe ni publica
  nada: el trabajo queda en rojo y GitHub avisa. Nota: esto protege las ejecuciones creadas desde que existe este paso; las anteriores
  llevan grabado su workflow antiguo y solo se pueden eliminar desde la pestaña Actions.
- **Registro limpio:** cada línea de `estado/ejecuciones.log` se construye en Python con todos los campos limpios (sin saltos de línea,
  controles ni caracteres invisibles, sin «|», recortados): el texto de «origen» no puede falsificar entradas.
- **Instrucción desarmada:** tras una acción extraordinaria por `push` (forzar, prueba, restaurar), `disparar/accion.txt` vuelve solo a
  `ciclo` en el mismo commit de estado, también si la acción falló (queda anotado en el registro; repetirla a mano si procede).
- **Restaurar** nunca deja que una PRUEBA sustituya a una publicación OFICIAL.
- Al guardar el estado, si `main` se movió, se reintenta una vez sobre la versión actual (nunca se despliega nada si el guardado falla).

## Seguridad
- La clave privada de firma **solo** está en *Settings → Secrets and variables → Actions → `HORARIOS_FIRMA_PRIVADA`*. El workflow la escribe en
  el disco de la máquina efímera, la usa y la borra. No aparece en el código ni en archivos públicos. La app solo lleva la clave pública.
- La app verifica la firma y las huellas SHA-256 antes de activar nada, y rechaza manifiestos con secuencia menor (copias viejas).
- Si la clave se perdiera o se filtrara: generar otra (`python tools/firma_manifiesto.py --generar-claves`), cambiar el secreto y sacar una versión
  de la app con la nueva clave pública (la app admite una lista de claves para el relevo).

## Fiabilidad de las ejecuciones automáticas
- Los cron de GitHub se retrasan o se saltan (medido el 20/11: el de las 04:17 UTC corrió a las 13:07 UTC). Por eso hay 5 disparadores
  al día (01:11, 04:17, 08:43, 15:47 y 21:29 UTC): la regla de «una comprobación real cada 20 h» hace que solo uno trabaje y los demás
  terminen en «OMITIDO». Cada ejecución queda anotada en `estado/ejecuciones.log` (evento, origen, resultado).
- Respaldo externo independiente de GitHub (opcional, gratuito): ver `docs/RESPALDO_EXTERNO.md`.
- Ejecución desde git, sin botones: editar `disparar/accion.txt` (línea 1: ciclo, ciclo_forzado, prueba_controlada o restaurar; línea 2: versión
  para restaurar) y subirlo.
- **Tras un fallo** (descarga cortada, error inesperado o de firma) NO se espera otras 20 h: no cuenta como comprobación correcta y se reintenta
  a las 3 h, hasta 3 intentos fallidos seguidos; después se vuelve a la espera normal de 20 h. Solo una comprobación CORRECTA (sin novedades,
  publicada o rechazada por su contenido) activa la regla de «una comprobación cada 20 h», que no ha cambiado.

## Mantenimiento
- Mirar de vez en cuando la pestaña **Actions**: cada día debe haber una ejecución. «SIN_CAMBIOS» o «OMITIDO» es lo normal.
- Si una ejecución sale en rojo por «Servicio que había y ya no está»: es que el Consorcio publicó un feed incompleto. No hay que hacer nada;
  cuando publique otro completo se procesará solo. Si una línea se suprime de verdad, añadirla (con motivo) a `tools/regresiones_explicadas.json`.
- GitHub desactiva los programas de repositorios sin actividad; aquí el propio trabajo escribe `estado/ultimo_latido.txt` cada día para evitarlo.
