# Changelog

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
