/** The product name, and the tab-title shape every page uses. */
export const PRODUCT = 'Phosphor';

export function pageTitle(page: string): string {
  return `${page} · ${PRODUCT}`;
}
