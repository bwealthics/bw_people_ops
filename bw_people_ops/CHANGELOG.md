# Changelog

## 18.0.2.1.0 (2026-11-25)

### Añadido
- Formulario del trabajador (`bw.people.intake`, CONTRACT.md Versión 2): la
  empresa abre un enlace personal (`intake_open`, 7 días) y el trabajador,
  tras un código de 6 dígitos enviado por correo a su correo personal,
  completa dirección, nacionalidad, fecha de nacimiento, teléfono y contacto
  de emergencia. Al enviar, los datos se escriben en la ficha del empleado.
- Reglas del código: 10 minutos, un solo uso, 5 intentos fallidos bloquean
  30 minutos, reenvío cada 60 s y máximo 3 por hora. Sesión de 30 minutos
  de inactividad y 4 horas en total.
- Token, código y sesión se guardan solo como hash (el código con HMAC y
  una sal en `bw_people_ops.intake_pepper`). Nunca se aceptan SSN, TIN ni
  datos bancarios: cualquier clave fuera de la lista se rechaza.
- Cron diario que vence las invitaciones y borra las respuestas parciales de
  las vencidas o canceladas. Menú "Formularios del trabajador" con el botón
  "Cancelar invitación".
- Parámetro `bw_people_ops.portal_base_url` para el enlace del correo.
- Los correos (invitación y código) salen dentro de la transacción y se
  borran después, salgan o no: ni el enlace ni el código quedan en
  `mail.mail`. Si el correo falla, la respuesta es `mail_failed`, no se
  guarda el token nuevo y no se consume la cuota de códigos.
- El teléfono del trabajador se guarda en `private_phone` (en Odoo 18
  `mobile_phone` es el móvil de trabajo); la clave del formulario sigue
  siendo `mobile_phone`.
- El bloqueo de 5 intentos no se reinicia al pedir otro código; a los 20
  fallos la invitación se cancela.
- Enviar es idempotente: la sesión sigue viva para mostrar la confirmación
  en solo lectura y guardar después responde `conflict`. Reabrir un
  formulario enviado exige `force=True`.
- Un código de estado que no existe en el país de residencia es un error
  de validación. Argumentos con tipo incorrecto responden `validation`.
- Migración: crea la sal `bw_people_ops.intake_pepper` antes de la primera
  petición.

## 18.0.2.0.3 (2026-11-25)

### Añadido
- Contratos y NDA: uso exclusivo de las herramientas de IA aprobadas por la
  empresa, con cuentas de la empresa; prohibidas las cuentas personales y los
  modelos de origen chino o alojados fuera de los Estados Unidos.

## 18.0.2.0.2 (2026-11-25)

### Cambiado
- Contrato y NDA: logo de Bwealthics, títulos más sobrios, texto más grande y
  formato de papel propio (Letter, márgenes fijos). Casillas de firma
  recalibradas para ese formato.

## 18.0.2.0.1 (2026-11-24)

### Corregido
- Casillas de firma y fecha del contrato y del NDA: posiciones medidas sobre
  el PDF real de producción.
- El firmante de la empresa (responsable de RR.HH.) ahora se ve y se edita en
  la pestaña "BW Documents".

## 18.0.2.0.0 (2026-11-24)

### Añadido
- Accesos (`bw.access.grant`): estado de provisión, referencia externa y
  mensaje de error; un solo acceso activo por persona y sistema.
- Contrato: campos para prellenar el documento (FLSA, modalidad, horario,
  alcance, compensación, bono, beneficios, facturación, preaviso).
- Empleado: idioma del documento y formulario fiscal (W-4, W-9, W-8BEN)
  calculado por tipo y país de residencia, editable.
- Reportes PDF del NDA y del contrato (empleo o contratista, español o
  inglés) y botón "Send BW Documents" que crea las solicitudes de firma
  enlazadas al contrato. El NDA se omite si el contrato ya tiene uno firmado.
- W-9 y W-8BEN prellenados desde la ficha; el número fiscal lo escribe la
  persona al firmar. Queda en el PDF firmado y en los valores de la
  solicitud de firma de Odoo Sign; no pasa por el portal, n8n ni Telegram.

### Corregido
- Puerta de activación: ya no clasifica por el nombre de la persona ni cuenta
  un formulario fiscal como contrato o NDA.
- Puerta de activación: el salto por contexto (`bw_skip_gate`) solo vale para
  el superusuario o un administrador del sistema.

### Cambiado
- `system` pasa de texto libre a lista cerrada. La migración conserva el
  texto anterior en las notas y lo clasifica como "Other".

## 18.0.1.0.1 (2026-07-13)

### Corregido
- Puerta de activación: normaliza `_ - .` en los nombres de plantilla.

## 18.0.1.0.0 (2026-07-12)

### Añadido
- Puerta de activación, registro de accesos, dueño secundario de cliente.
