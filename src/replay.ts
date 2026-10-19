import './styles/app.css';
import { mount } from 'svelte';
import ReplayPage from './components/ReplayPage.svelte';

mount(ReplayPage, { target: document.getElementById('app')! });
