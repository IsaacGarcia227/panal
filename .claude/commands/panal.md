 # Inicializar Panal Inteligente

Ejecuta los siguientes pasos en orden para inicializar el sistema completo:

## 1. Iniciar el servidor Flask

Corre en background desde la carpeta `server/`:
```
python "c:\Users\DELL Latitude 5520\Documents\ITE\8vo SEMESTRE\panal\server\servidor.py"
```

Espera 3 segundos y verifica que el servidor esté corriendo en el puerto 8081.

## 2. Iniciar el túnel Cloudflare

Corre en background:
```
& "C:\Program Files (x86)\cloudflared\cloudflared.exe" tunnel --url http://localhost:8081
```

Espera 10 segundos y lee el output del proceso para extraer la URL del túnel. Busca la línea que contiene `trycloudflare.com` y extrae la URL completa `https://xxxx.trycloudflare.com`.

## 3. Actualizar config.h

Actualiza la línea `#define SERVER_URL` en el archivo:
`c:\Users\DELL Latitude 5520\Documents\ITE\8vo SEMESTRE\panal\firmware\config.h`

Con la nueva URL del túnel obtenida en el paso anterior.

## 4. Reportar

Muestra al usuario:
- Confirmación de que el servidor Flask está corriendo
- La nueva URL del túnel Cloudflare
- Aviso de que debe re-subir el sketch al ESP32 con la nueva URL
