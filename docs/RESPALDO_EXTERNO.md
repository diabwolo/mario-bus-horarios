# Respaldo externo (segundo mecanismo, independiente de los cron de GitHub)

**Para qué:** los cron de GitHub Actions se retrasan o se saltan (medido: el de las 04:17 UTC corrió a las 13:07 UTC). Un servicio
externo gratuito llama una vez al día a GitHub y dispara el mismo trabajo. Si GitHub ya hizo la comprobación hace menos de 20 h,
el trabajo termina en «OMITIDO» sin descargar ni publicar nada, así que no hay descargas duplicadas.

**Seguridad:** el servicio externo solo guarda un token de GitHub con el permiso mínimo: *un solo repositorio* (`mario-bus-horarios`)
y *solo* «Actions: lectura y escritura» (sirve para lanzar el trabajo; NO permite leer el secreto de la clave, ni cambiar código, ni
borrar nada). El token lo crea y lo pega el dueño; nunca se escribe en el repositorio, en el chat ni en un archivo.

## Qué hace el dueño (una sola vez, ~10 minutos)
1. **Crear el token** en GitHub: Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → *Generate new token*.
   - Nombre: `mario-bus-respaldo` · Caducidad: 1 año (apuntar la fecha para renovarlo).
   - *Repository access*: **Only select repositories** → `mario-bus-horarios`.
   - *Repository permissions*: **Actions → Read and write** (lo demás en «No access»; «Metadata: Read» se pone solo).
   - *Generate token* y copiarlo (empieza por `github_pat_…`).
2. **Crear la tarea** en https://cron-job.org (cuenta gratuita, sin tarjeta) → *Create cronjob*:
   - URL: `https://api.github.com/repos/diabwolo/mario-bus-horarios/actions/workflows/horarios.yml/dispatches`
   - Frecuencia: cada día a las 03:15 (zona horaria Europe/Madrid).
   - *Advanced* → Request method: **POST** · Request body: `{"ref":"main","inputs":{"origen":"respaldo-externo"}}`
   - Headers: `Authorization: Bearer <EL TOKEN>` · `Accept: application/vnd.github+json` · `X-GitHub-Api-Version: 2022-11-28` · `Content-Type: application/json`
   - Respuesta correcta esperada: **204**.
3. Pulsar *Test run* en cron-job.org. En unos minutos aparece una línea con `origen=respaldo-externo` en `estado/ejecuciones.log`.

## Cómo se comprueba que funciona
`estado/ejecuciones.log` anota cada ejecución: `fecha | ejecución # | evento | origen | cron | acción | resultado`.
- Cron de GitHub → `evento=schedule`. Respaldo externo → `evento=workflow_dispatch | origen=respaldo-externo`. Manual → `origen=manual`.

## Renovar o anular
Si el token caduca, se filtra o se quiere quitar el respaldo: borrarlo en GitHub (Settings → Developer settings → Tokens) y la tarea
de cron-job.org dejará de funcionar sin afectar a nada más.
