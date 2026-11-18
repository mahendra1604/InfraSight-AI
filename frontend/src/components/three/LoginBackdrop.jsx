import React from 'react';
import { useMotionSetting } from '../../motion';
import { useUi } from '../../ui';
import BackgroundWorld from './BackgroundWorld';
import { DEMO_SITE } from './SiteScene';

/** The same 3D world as inside the app, with a demo site (no project data is loaded before sign-in). */
export default function LoginBackdrop() {
  const { reduced } = useMotionSetting();
  const { theme } = useUi();
  return <BackgroundWorld model={DEMO_SITE} tab="Login" theme={theme} reduced={reduced}/>;
}
