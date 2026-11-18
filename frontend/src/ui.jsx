import React, { createContext, useContext, useEffect, useState } from 'react';
import { motion, useMotionValue, useSpring, useTransform } from 'motion/react';
import { useMotionSetting } from './motion';

const THEME_KEY = 'infrasight.theme';
const UiContext = createContext(null);

function stored(key, fallback) {
  try { return localStorage.getItem(key) || fallback; } catch { return fallback; }
}

/** Theme (dark command center / light glass), presentation mode, and the "focused" activity
 *  that the background 3D world highlights (e.g. while hovering a report). */
export function UiProvider({ children }) {
  // ?theme=light|dark in the URL wins (handy for demos), then the saved choice, then dark.
  const [theme, setTheme] = useState(() => new URLSearchParams(window.location.search).get('theme') || stored(THEME_KEY, 'dark'));
  const [presentation, setPresentation] = useState(false);
  const [focus, setFocus] = useState(null);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* private mode */ }
  }, [theme]);
  useEffect(() => {
    if (!presentation) return undefined;
    const onKey = (e) => { if (e.key === 'Escape') setPresentation(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [presentation]);
  const value = { theme, toggleTheme: () => setTheme((t) => (t === 'dark' ? 'light' : 'dark')), presentation, setPresentation, focus, setFocus };
  return <UiContext.Provider value={value}>{children}</UiContext.Provider>;
}

export const useUi = () => useContext(UiContext);

/** Card that tilts toward the pointer in 3D. */
export function TiltCard({ children, className, variants, max = 7, ...rest }) {
  const { reduced } = useMotionSetting();
  const x = useMotionValue(0.5);
  const y = useMotionValue(0.5);
  const rotateX = useSpring(useTransform(y, [0, 1], [max, -max]), { stiffness: 220, damping: 18 });
  const rotateY = useSpring(useTransform(x, [0, 1], [-max, max]), { stiffness: 220, damping: 18 });
  const glowX = useTransform(x, (v) => `${v * 100}%`);
  const glowY = useTransform(y, (v) => `${v * 100}%`);
  function onMove(e) {
    if (reduced) return;
    const r = e.currentTarget.getBoundingClientRect();
    x.set((e.clientX - r.left) / r.width);
    y.set((e.clientY - r.top) / r.height);
  }
  function onLeave() { x.set(0.5); y.set(0.5); }
  return <motion.article className={className} variants={variants} onPointerMove={onMove} onPointerLeave={onLeave}
    style={reduced ? undefined : { rotateX, rotateY, transformPerspective: 800 }} {...rest}>
    {!reduced && <motion.span className="tilt-glow" style={{ '--gx': glowX, '--gy': glowY }}/>}
    {children}
  </motion.article>;
}
