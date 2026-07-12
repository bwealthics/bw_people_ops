# bw_people_ops

People Ops para Odoo 18.0 Enterprise (BWEALTHICS): compuerta de activación de contratos por firmas, registro de accesos y dueño secundario de clientes.

## Qué hace

1. **Compuerta de activación** (`hr.contract`): un contrato no puede pasar a *Running* (ni crearse directamente en Running, ni vía import) hasta que el **NDA** y el **contrato de empleo / ICA** estén con firma completa en la pestaña de firmas del propio contrato (`hr_contract_sign`). Los formularios fiscales (W-4/W-9/W-8) y la identificación NO bloquean ni satisfacen la compuerta. El cron nocturno de contratos nunca se aborta: los contratos que fallan se saltan con warning en el log. Escape para correcciones de datos: contexto `bw_skip_gate=True`.

2. **Access Grants** (`bw.access.grant`, menú Empleados → Access Grants, grupo HR Officer): registro de accesos por persona y sistema con perfil (Empleado, Contractor, Manager, Ejecutivo, Cliente, Temporal), expiración (obligatoria para Contractor/Temporal/Cliente), excepciones con razón obligatoria y **referencia al ítem de 1Password (nunca el secreto)**. Chatter para trazabilidad.

3. **Dueño secundario** (`res.partner.secondary_owner_id`): respaldo de dueño por cliente (regla de bus-factor).

## Nombres de plantillas Sign (IMPORTANTE)

La compuerta clasifica las firmas del contrato por el nombre de la plantilla/referencia:

| Documento | El nombre DEBE contener | Ejemplos válidos |
|---|---|---|
| NDA | `NDA` (palabra), `Non-Disclosure`, `Confidencialidad`, `Confidentiality` | "NDA BWEALTHICS", "Acuerdo de Confidencialidad" |
| Contrato / ICA | `Agreement`, `Contract`, `Contrato`, `Acuerdo` o `ICA` (palabra) | "Employment Agreement", "ICA - Advisor", "Contrato de servicios" |
| Formularios fiscales e ID | NINGUNA de las palabras anteriores | "W-9", "W-8BEN", "W-4" |

## Instalación

- Depends: `hr_contract_sign` (Enterprise; arrastra hr, hr_contract, sign, mail).
- Copiar `bw_people_ops/` al addons path (deploy Binhex) → Actualizar lista de aplicaciones → Instalar "BW People Ops".
- Tests: `--test-tags /bw_people_ops`.

## Post-instalación

1. Verificar la compuerta: contrato en borrador → intentar Running sin firmas → debe bloquear.
2. Asignar dueño secundario a los clientes (ficha del contacto).
3. Registrar los accesos vigentes en Access Grants (solo metadata + referencia 1Password).

Licencia: LGPL-3. Parte del framework People Ops de BWEALTHICS (docs operativos en el vault, `ODOO_MODULES/onboardingHR/`).
