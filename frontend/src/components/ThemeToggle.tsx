import { useTheme, type Theme } from "../theme";

const NEXT: Record<Theme, Theme> = { system: "light", light: "dark", dark: "system" };
const LABEL: Record<Theme, string> = {
  system: "System theme",
  light: "Light theme",
  dark: "Dark theme",
};
const ICON: Record<Theme, string> = { system: "◐", light: "☀", dark: "☾" };

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  return (
    <button
      type="button"
      onClick={() => setTheme(NEXT[theme])}
      title={`${LABEL[theme]} (click for ${LABEL[NEXT[theme]].toLowerCase()})`}
      aria-label={`${LABEL[theme]}. Switch to ${LABEL[NEXT[theme]].toLowerCase()}.`}
      className="flex size-8 items-center justify-center rounded-lg text-base text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:outline-none dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
    >
      <span aria-hidden="true">{ICON[theme]}</span>
    </button>
  );
}
