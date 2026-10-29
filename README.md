# Ventisca

App web para **empresas de mantenimiento de aires acondicionados**: agenda de visitas, órdenes de servicio, plan de mantenimiento preventivo con **alertas automáticas**, clientes y sus equipos, garantías, cotizaciones, compra-venta de aires, materiales, técnicos, nómina, cartera y reportes. Hecha por SoftGest.

## Módulos

| Módulo | Qué resuelve |
|---|---|
| **Mantenimientos (alertas)** | Al entrar se abre una ventana con los clientes que tienen el preventivo **vencido o por vencer**. Desde ahí se agenda la visita, se envía el recordatorio por **WhatsApp** (mensaje listo) o se pospone. La lista completa está en *Mantenimientos*. |
| **Clientes y equipos** | Cada cliente con sus aires: tipo, BTU, marca, refrigerante, inverter, serial, ubicación y **frecuencia de preventivo** (cada 1, 2, 3, 4, 6 o 12 meses). Hoja de vida de cada equipo. |
| **Agenda** | Visitas de los próximos días por técnico, atrasadas y terminadas hoy. El técnico ve solo las suyas desde el celular. |
| **Órdenes de servicio** | Preventivo, correctivo, instalación, desinstalación, diagnóstico y carga de gas. Equipos atendidos, cobro por equipo, materiales usados, pago al técnico, transporte. Al terminarla: descuenta materiales, calcula la ganancia y **actualiza el próximo preventivo** de cada equipo. Documento en **PDF e imagen para WhatsApp**. |
| **Garantías** | Cada correctivo/instalación da garantía (30 días por defecto). Si el equipo vuelve a fallar en ese plazo, la app avisa y la visita se registra **en garantía, sin cobro**. |
| **Cotizaciones** | Servicios y repuestos, validez, PDF/imagen y botón para convertirla en orden cuando el cliente la aprueba. |
| **Venta de equipos** | Compra-venta sin inventario: costo del proveedor + flete vs. precio al cliente = ganancia. Puede agregar el equipo a la ficha del cliente y **agendar la instalación**. |
| **Materiales y compras** | Gas, tubería, capacitores, filtros… con costo promedio, precio al cliente, alerta de mínimo y conteo físico. |
| **Técnicos y nómina** | Pago por comisión (% o valor fijo por servicio), salario fijo o mixto. La nómina suma sola las comisiones pendientes, salario, auxilio, aportes y prestaciones. Desprendible en PDF. |
| **Cartera** | Órdenes y ventas a crédito, vencidas, abonos y recordatorio de pago por WhatsApp. |
| **Resumen y reportes** | Ingresos, costos, ganancia neta, servicios por tipo, por técnico, por cliente. Excel completo con la agenda de preventivos de 90 días. |
| **Gastos, herramientas y distribución** | Combustible, arriendo, celulares…; herramientas con depreciación; regla de reparto del dinero. |
| **Usuarios** | Administrador, Asesor / recepción y Técnico. |

### Cómo se calcula la ganancia
1. Cada compra actualiza el costo promedio del material.
2. Cada orden terminada: `cobrado − materiales (al costo) − pago al técnico − transporte = ganancia`.
3. Cada venta de equipo: `precio − costo del proveedor − flete = ganancia`.
4. Ganancia neta = ganancia de órdenes y equipos − nómina (sin comisiones, que ya van en cada orden) − gastos − desgaste de herramientas.

> Los documentos son soporte del servicio; **no reemplazan la factura electrónica DIAN**.

## Desplegar en Render + Supabase

1. **Supabase** → *Connect* → **Session pooler** (puerto 5432), con la contraseña real. Puede ser la misma base de otras apps: las tablas usan el prefijo `vt_`.
2. **Render** → *New → Web Service* con este repo:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app --workers 2 --threads 4 --timeout 60`
3. Variables de entorno:
   - `DATABASE_URL` → la de Supabase
   - `SECRET_KEY` → texto largo aleatorio
   - `NOMBRE_EMPRESA` → nombre de la empresa del cliente
   - `ADMIN_USER` → usuario del administrador (por defecto `admin`)
   - `ADMIN_PASSWORD` → clave del administrador
   - `PYTHON_VERSION` → `3.12.4`
4. Al arrancar por primera vez se crea la empresa con ese administrador. Entra y completa **Datos del negocio** (logo, NIT, teléfono) y **Configuración**.

Página de carga mientras Render despierta la app: `softgestapps.com/ventisca/` (usa la ruta `/salud`).

`demo.py` llena la base con datos de ejemplo; solo se usa en un computador de pruebas (capturas del manual).
