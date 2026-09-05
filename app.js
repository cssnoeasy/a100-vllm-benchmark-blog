const root = document.documentElement;
const themeToggle = document.querySelector('#theme-toggle');
const menuToggle = document.querySelector('#menu-toggle');
const sidebar = document.querySelector('#left-sidebar');
const backdrop = document.querySelector('#mobile-backdrop');

if (localStorage.getItem('inference-theme') === 'dark') root.classList.add('dark');
themeToggle.addEventListener('click', () => {
  root.classList.toggle('dark');
  localStorage.setItem('inference-theme', root.classList.contains('dark') ? 'dark' : 'light');
});

function closeMenu() { sidebar.classList.remove('open'); backdrop.classList.remove('open'); }
menuToggle.addEventListener('click', () => { sidebar.classList.toggle('open'); backdrop.classList.toggle('open'); });
backdrop.addEventListener('click', closeMenu);
sidebar.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeMenu));

const progress = document.querySelector('#reading-progress');
const tocLinks = [...document.querySelectorAll('.right-toc a')];
const sections = tocLinks.map((link) => document.querySelector(link.getAttribute('href'))).filter(Boolean);
function updateReadingState() {
  const scrollable = document.documentElement.scrollHeight - window.innerHeight;
  progress.style.width = `${scrollable ? (window.scrollY / scrollable) * 100 : 0}%`;
  let current = sections[0];
  sections.forEach((section) => { if (section.getBoundingClientRect().top <= 145) current = section; });
  tocLinks.forEach((link) => link.classList.toggle('active', link.getAttribute('href') === `#${current.id}`));
}
window.addEventListener('scroll', updateReadingState, { passive: true });
updateReadingState();
