# Requisitos de hardware para encapsular AureaIA VMS como demo comercial

## Objetivo

Este documento define una base de hardware para entregar AureaIA VMS como
demo instalable en una notebook, mini PC o equipo industrial. La aplicación
corre localmente, procesa los streams RTSP y ejecuta las analíticas en CPU.
No requiere nube ni una GPU dedicada para una demo controlada.

Las cantidades son referencias de dimensionamiento. Antes de una entrega
comercial definitiva se debe probar el equipo con las cámaras, resolución,
codec y analíticas exactas del cliente.

## Perfil recomendado para una demo comercial

| Componente | Recomendación |
|---|---|
| CPU | Intel Core i7 de 12.ª generación o superior, o AMD Ryzen 7 equivalente |
| Núcleos/hilos | 8 núcleos físicos como referencia; 12 o más hilos |
| RAM | 32 GB DDR4/DDR5 |
| Disco | SSD NVMe de 1 TB |
| GPU | No obligatoria; GPU integrada moderna es suficiente |
| Red | Ethernet Gigabit; Wi-Fi solo como respaldo |
| Sistema operativo | Windows 10/11 de 64 bits o Linux 64 bits |
| Pantalla | 1920×1080 como mínimo; 2560×1440 recomendado |
| Alimentación | UPS para notebook, mini PC o equipo industrial |

Este perfil permite mostrar una grilla de hasta cuatro cámaras, analíticas
activas de forma selectiva, overlays, alarmas, clips de evento y exportación
de evidencia sin que el equipo quede al límite durante una presentación.

## Perfil mínimo para una demo reducida

| Componente | Mínimo práctico |
|---|---|
| CPU | Intel Core i5 de 10.ª generación o AMD Ryzen 5 equivalente |
| Núcleos/hilos | 4 núcleos físicos y 8 hilos |
| RAM | 16 GB |
| Disco | SSD de 512 GB, con al menos 100 GB libres |
| GPU | No obligatoria |
| Red | Ethernet Gigabit o Wi-Fi 5 estable |
| Sistema operativo | Windows 10/11 de 64 bits o Linux 64 bits |
| Pantalla | 1920×1080 |

El perfil mínimo debe limitarse a una o dos cámaras y a las analíticas
necesarias para la presentación. No se recomienda habilitar todas las
analíticas pesadas a la máxima frecuencia en este perfil.

## Perfil recomendado para cuatro cámaras

Para una demo con cuatro cámaras RTSP simultáneas:

- 32 GB de RAM.
- CPU de 8 núcleos físicos o más.
- SSD NVMe, no disco mecánico.
- Ethernet Gigabit conectada al mismo switch de las cámaras.
- Cámaras configuradas preferentemente con H.264 para máxima compatibilidad.
- Subflujo de baja resolución para la grilla normal y flujo principal para
  Vista Inteligente, configuración y expansión.
- Analíticas activadas según la escena; no es necesario ejecutar las cuatro
  analíticas en las cuatro cámaras.

Como regla inicial de presentación, se recomienda:

| Cantidad de cámaras | Analíticas simultáneas sugeridas | CPU/RAM |
|---:|---|---|
| 1 | Todas las necesarias para la escena | Core i5 / 16 GB |
| 2 | 2 a 4 analíticas totales | Core i5 o Ryzen 5 / 16 GB |
| 4 | 4 a 8 analíticas totales | Core i7 o Ryzen 7 / 32 GB |
| 8 | Requiere benchmark dedicado | Core i9/Ryzen 9 o servidor / 32-64 GB |

Estas cifras no sustituyen una medición. Detección facial y analíticas
basadas en detección de objetos consumen considerablemente más CPU que
movimiento o estado de puerta.

## GPU: cuándo hace falta

La versión actual está diseñada para funcionar en CPU y utiliza modelos
locales. Para una demo comercial estándar:

- **GPU dedicada:** no requerida.
- **GPU integrada:** aceptable para la interfaz y la decodificación,
  siempre que el CPU tenga margen.
- **GPU recomendada:** útil para escalar a muchas cámaras, mayor FPS de
  inferencia o analíticas simultáneas en múltiples canales.

Si se agrega aceleración por GPU en una futura versión, se debe validar el
driver, el runtime de inferencia y la distribución de la aplicación. No se
debe condicionar el instalador demo a una GPU específica sin una prueba de
compatibilidad.

## Red y cámaras

### Red local

- Switch Gigabit dedicado o con capacidad suficiente.
- Cableado Cat5e o superior.
- IP fija o reservas DHCP para las cámaras.
- Latencia baja y pérdida de paquetes cercana a cero.
- Separación de la red de cámaras de la red de invitados cuando el cliente
  lo permita.

### Ancho de banda orientativo

El consumo depende del codec, resolución, FPS y bitrate configurado. Como
referencia:

| Stream | Bitrate orientativo |
|---|---:|
| 1080p H.264 | 2-6 Mbps |
| 1080p H.265 | 1.5-4 Mbps |
| 4K H.264/H.265 | 6-16 Mbps |

Para cuatro cámaras 1080p, una red Gigabit ofrece margen suficiente. El
tráfico debe calcularse considerando que puede existir más de un stream por
cámara y que la aplicación puede mantener flujo principal, subflujo y
buffer de eventos.

### Compatibilidad

Para la demo se recomienda:

- RTSP sobre TCP.
- H.264 como primera opción.
- H.265/HEVC solo después de probar el equipo y sus decodificadores.
- GOP y bitrate estables.
- Subflujo configurado en cada cámara.
- Usuario RTSP/ONVIF exclusivo para la aplicación.

Los mensajes `VPS/PPS` de HEVC o desconexiones recurrentes indican que se debe
revisar codec, perfil, firmware, transporte TCP y capacidad de decodificación
antes de presentar el sistema al cliente.

## Almacenamiento y retención

La aplicación conserva clips, capturas y evidencia en el directorio de datos.
El espacio requerido depende del bitrate y de la retención:

```text
GB aproximados = bitrate_Mbps × segundos × cámaras / 8 / 1024
```

Para una demo con clips de eventos, 512 GB son suficientes. Para grabación
continua, el cálculo debe hacerse con el bitrate real de cada cámara.

Recomendaciones:

- SSD con al menos 100 GB libres al iniciar la demo.
- 1 TB para una demo comercial con margen, modelos, logs y media.
- Activar retención automática.
- Evitar que el sistema operativo y la media compartan un disco casi lleno.
- Exportar evidencia a un disco externo o carpeta del cliente si se necesita
  conservarla después de la demo.

## Encapsulado del producto

El equipo de entrega debe incluir:

1. Instalador o ejecutable firmado cuando el proceso de firma esté
   disponible.
2. Modelos de inferencia incluidos en el paquete o descargados durante una
   instalación controlada.
3. Directorio de datos separado del directorio de instalación.
4. Base SQLite inicializada automáticamente.
5. Usuario administrador inicial con cambio obligatorio de contraseña.
6. Acceso directo para iniciar AureaIA VMS.
7. Runtime de Microsoft Visual C++ en Windows, si el empaquetado lo requiere.
8. Drivers de video, red y cámara actualizados.
9. Plantilla de configuración para cámaras de demo.
10. Datos de demo claramente separados de datos reales del cliente.

El directorio de datos debe permanecer escribible por el usuario. No se debe
guardar la base ni los clips dentro de `Program Files` ni dentro de un
directorio temporal de un ejecutable one-file.

## Accesorios recomendados para la presentación

- UPS de 600 VA o superior para un mini PC y switch pequeño.
- Segundo monitor opcional para mostrar Vista en Vivo y Alarmas en paralelo.
- Switch Gigabit de repuesto.
- Cableado de red de repuesto.
- Cámara IP de respaldo o rig RTSP local.
- Pendrive o SSD externo con instalador y videos de demo.
- Mouse y teclado dedicados si se presenta en gabinete industrial.

## Checklist de aceptación antes de vender la demo

- [ ] El equipo arranca la aplicación en menos de un minuto.
- [ ] Las cuatro cámaras se conectan sin intervención manual.
- [ ] La Vista Inteligente usa el flujo principal y las ROI quedan alineadas.
- [ ] Las analíticas configuradas generan eventos y actualizan el panel.
- [ ] Los clips y capturas se escriben correctamente.
- [ ] La exportación de evidencia funciona.
- [ ] La aplicación continúa operativa durante al menos 30 minutos.
- [ ] CPU, RAM, temperatura y disco tienen margen observable.
- [ ] Se probó la desconexión y reconexión de una cámara.
- [ ] Se verificó que el equipo tenga espacio libre suficiente.
- [ ] Se dispone de un plan B con cámaras falsas o clips RTSP locales.

## Recomendación final

Para una demo comercial portable, la configuración de referencia es una mini
PC o notebook con **Core i7/Ryzen 7, 32 GB de RAM, SSD NVMe de 1 TB,
Ethernet Gigabit y pantalla Full HD**. Esta configuración evita presentar el
producto al límite y deja margen para mostrar Vista Inteligente, alarmas,
clips y varias analíticas sin depender de una GPU dedicada.
