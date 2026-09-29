import { createRoot } from 'react-dom/client';
import { MainWindow } from './MainWindow';
import { TrayPopup } from './TrayPopup';
import './style.css';

// One bundle, two surfaces: the main window and the menu-bar popup (index.html#tray).
const isTray = window.location.hash === '#tray';
if (isTray) document.documentElement.classList.add('tray');

createRoot(document.getElementById('root')!).render(isTray ? <TrayPopup /> : <MainWindow />);
