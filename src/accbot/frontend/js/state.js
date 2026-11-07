import { api } from "./api.js";
export const state = {
  plugins: [],
  models: [],
  personas: [],
  prompts: [],
  connections: [],
  secrets: [],
  status: { active: [], connections: [] },
  ready: false,
};
const listeners = new Set();
export function subscribe(handler) {
  listeners.add(handler);
  return () => listeners.delete(handler);
}
export function emit(kind = "data") {
  for (const handler of listeners) handler(kind);
}
export async function refresh() {
  const [plugins, models, personas, prompts, connections, secrets, status] =
    await Promise.all([
      api("plugins"),
      api("models"),
      api("personas"),
      api("prompts"),
      api("adapters/onebot"),
      api("secrets"),
      api("status"),
    ]);
  Object.assign(state, {
    plugins,
    models,
    personas,
    prompts,
    connections,
    secrets,
    status,
    ready: true,
  });
  emit();
}
export async function poll() {
  try {
    state.status = await api("status");
    emit("status");
  } catch {
    emit("offline");
  }
}
