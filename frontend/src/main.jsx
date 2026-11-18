import React from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { MotionProvider } from './motion';
import { UiProvider } from './ui';
import './design.css';

createRoot(document.getElementById('root')).render(<MotionProvider><UiProvider><App/></UiProvider></MotionProvider>);
