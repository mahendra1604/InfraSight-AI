import React, { createContext, useContext, useEffect, useState } from 'react';
import { animate, motion, MotionConfig, useReducedMotion } from 'motion/react';

const KEY = 'infrasight.motion';
const MotionSetting = createContext({ reduced: false, toggle: () => {} });

function readSetting() {
  try { return localStorage.getItem(KEY); } catch { return null; }
}

/** App-wide animation switch. Defaults to the operating-system "reduce motion" setting. */
export function MotionProvider({ children }) {
  const osReduced = useReducedMotion();
  const [choice, setChoice] = useState(readSetting);          // 'on' | 'off' | null (follow OS)
  const reduced = choice ? choice === 'off' : Boolean(osReduced);
  const toggle = () => {
    const next = reduced ? 'on' : 'off';
    setChoice(next);
    try { localStorage.setItem(KEY, next); } catch { /* private mode */ }
  };
  return <MotionSetting.Provider value={{ reduced, toggle }}>
    <MotionConfig reducedMotion={reduced ? 'always' : 'never'}>{children}</MotionConfig>
  </MotionSetting.Provider>;
}

export const useMotionSetting = () => useContext(MotionSetting);

const ease = [0.22, 1, 0.36, 1];

export function FadeIn({ children, delay = 0, y = 14, className, as = 'div', ...rest }) {
  const Component = motion[as];
  return <Component className={className} initial={{ opacity: 0, y }} animate={{ opacity: 1, y: 0 }}
    transition={{ duration: 0.5, delay, ease }} {...rest}>{children}</Component>;
}

export const staggerParent = { hidden: {}, show: { transition: { staggerChildren: 0.07, delayChildren: 0.05 } } };
export const staggerChild = { hidden: { opacity: 0, y: 16 }, show: { opacity: 1, y: 0, transition: { duration: 0.45, ease } } };

export function PageTransition({ children, id }) {
  return <motion.div key={id} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
    transition={{ duration: 0.28, ease }}>{children}</motion.div>;
}

/** Counts up to a number, e.g. <AnimatedNumber value={46.7} decimals={1} suffix="%"/>. */
export function AnimatedNumber({ value, decimals = 0, suffix = '', duration = 1.1 }) {
  const { reduced } = useMotionSetting();
  const target = Number(value) || 0;
  const [display, setDisplay] = useState(reduced ? target : 0);
  useEffect(() => {
    if (reduced) { setDisplay(target); return undefined; }
    const controls = animate(display, target, { duration, ease: 'easeOut', onUpdate: setDisplay });
    return () => controls.stop();
  }, [target, reduced]);
  return <>{display.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}{suffix}</>;
}

/** Horizontal bar that grows to `value` percent. */
export function GrowBar({ value, className, delay = 0, style }) {
  return <motion.div className={className} style={style} initial={{ width: 0 }} animate={{ width: `${Math.max(0, Math.min(100, value))}%` }}
    transition={{ duration: 0.9, delay, ease }}/>;
}

/** Document with a moving scan line, shown while the AI reads a file. */
export function ScanEffect({ label }) {
  return <div className="scan-effect" aria-hidden="true">
    <div className="scan-doc">
      {[78, 92, 64, 85, 70, 88, 55].map((w, i) => <span key={i} style={{ width: `${w}%` }}/>)}
      <motion.div className="scan-line" animate={{ top: ['6%', '90%', '6%'] }} transition={{ duration: 2.4, repeat: Infinity, ease: 'easeInOut' }}/>
    </div>
    {label && <small>{label}</small>}
  </div>;
}
