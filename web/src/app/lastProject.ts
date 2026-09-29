// The one thing the client keeps in web storage: which project's runs were
// open last, so / returns there. Storage may be refused; then / goes to default.
const KEY = 'vantage.lastProject';

export function readLastProject(): string | null {
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function writeLastProject(project: string): void {
  try {
    window.localStorage.setItem(KEY, project);
  } catch {
    // Not kept; / goes to default.
  }
}
