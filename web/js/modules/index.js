// Registro de páginas/abas. Para adicionar uma página nova:
//   1. crie web/js/modules/minha.js exportando { id, title, icon, mount(el, app), show?(), hide?() }
//   2. importe e adicione aqui na ordem desejada.
//   (opcional) exporte enabled(app) -> false para esconder a aba, ex.: recurso ausente.
import overview from "./overview.js";
import processes from "./processes.js";
import net from "./net.js";
import apps from "./apps.js";
import controls from "./controls.js";
import alerts from "./alerts.js";
import sensors from "./sensors.js";
import fan from "./fan.js";
import system from "./system.js";

export const MODULES = [overview, apps, controls, processes, net, sensors, fan, system, alerts];
