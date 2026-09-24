(() => {
  const launch = new Date(window.FIRST_VAULT.launchAt).getTime();
  const els = {
    d: document.getElementById('days'), h: document.getElementById('hours'),
    m: document.getElementById('minutes'), s: document.getElementById('seconds')
  };
  let opened = !!window.FIRST_VAULT.huntOpen;

  function pad(n){ return String(n).padStart(2,'0'); }
  function tick(){
    let diff = Math.max(0, launch - Date.now());
    const d = Math.floor(diff / 86400000); diff %= 86400000;
    const h = Math.floor(diff / 3600000); diff %= 3600000;
    const m = Math.floor(diff / 60000); diff %= 60000;
    const s = Math.floor(diff / 1000);
    if (els.d) els.d.textContent = pad(d);
    if (els.h) els.h.textContent = pad(h);
    if (els.m) els.m.textContent = pad(m);
    if (els.s) els.s.textContent = pad(s);
    if (!opened && launch <= Date.now()) { opened = true; location.reload(); }
  }
  tick(); setInterval(tick, 1000);

  const stars = document.getElementById('stars');
  if (stars && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    for (let i=0;i<74;i++) {
      const s=document.createElement('i'); s.className='star';
      s.style.left=Math.random()*100+'%'; s.style.top=Math.random()*100+'%';
      s.style.setProperty('--d',(2+Math.random()*5)+'s'); s.style.animationDelay=(-Math.random()*5)+'s';
      stars.appendChild(s);
    }
  }

  const toast = document.getElementById('toast');
  const signal = document.getElementById('signalButton');
  if (signal && toast) signal.addEventListener('click', () => {
    toast.classList.add('show');
    clearTimeout(window.__vaultToast);
    window.__vaultToast=setTimeout(()=>toast.classList.remove('show'),3800);
  });
  const shareVault = document.getElementById('shareVault');
  const copyVault = document.getElementById('copyVault');
  const shareUrl = 'https://internetbooty.com/?utm_source=share&utm_medium=organic&utm_campaign=first_vault';
  const shareText = "I found a $5,000 online treasure hunt called Internet Booty. There's a hidden early-access path somewhere on the homepage.";

  if (shareVault) shareVault.addEventListener('click', async () => {
    try {
      if (navigator.share) {
        await navigator.share({title:'Internet Booty — The First Vault', text:shareText, url:shareUrl});
      } else {
        await navigator.clipboard.writeText(shareUrl);
        shareVault.textContent = 'LINK COPIED ✓';
        setTimeout(()=>shareVault.textContent='SHARE THE VAULT →',1800);
      }
    } catch (_) {}
  });

  if (copyVault) copyVault.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      copyVault.textContent = 'COPIED ✓';
      setTimeout(()=>copyVault.textContent='COPY LINK',1800);
    } catch (_) {}
  });

})();
