(()=>{
 const tg=window.Telegram?.WebApp, root=document.documentElement;
 const status=document.querySelector('#status'), name=document.querySelector('#name'), avatar=document.querySelector('#avatar'), role=document.querySelector('#role');
 if(tg){tg.ready();tg.expand();status.textContent='Telegram';const u=tg.initDataUnsafe?.user;if(u){name.textContent=u.username?'@'+u.username:[u.first_name,u.last_name].filter(Boolean).join(' ')||'Telegram';avatar.textContent=(u.first_name||'?')[0].toUpperCase();}}
 else status.textContent='Браузер';
 // The bot remains the authority. This only improves presentation; it never grants access.
 const isAdminHint=!!(tg?.initDataUnsafe?.user?.is_bot===false);
 const params=new URLSearchParams(location.search), view=params.get('view');
 if(view==='admin'){document.querySelector('#admin').hidden=false;role.textContent='Панель доступа: проверяется ботом';}
 document.querySelectorAll('[data-send]').forEach(b=>b.onclick=()=>{
   let action=b.dataset.send, text='';
   if(action==='user_lookup') text=document.querySelector('#lookup')?.value.trim()||'';
   const payload={type:'action',action,text};
   if(tg){tg.sendData(JSON.stringify(payload));tg.close();}
   else alert('Откройте приложение через Telegram.');
 });
 const af=document.querySelector('#appealForm'), appealBtn=document.querySelector('[data-send="appeal"]');
 if(view==='appeal') af.hidden=false;
 if(appealBtn) appealBtn.onclick=()=>{af.hidden=false;af.scrollIntoView({behavior:'smooth'});};
 document.querySelector('#submitAppeal')?.addEventListener('click',()=>{
   const text=document.querySelector('#appealText')?.value.trim();
   if(!text){alert('Введите текст апелляции.');return;}
   if(tg){tg.sendData(JSON.stringify({type:'action',action:'appeal',text}));tg.close();}
 });
 addEventListener('mousemove',e=>{root.style.setProperty('--mx',e.clientX/innerWidth*100+'%');root.style.setProperty('--my',e.clientY/innerHeight*100+'%')},{passive:true});
})();
