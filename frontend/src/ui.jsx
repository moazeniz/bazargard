import React,{useEffect,useRef} from 'react';
import {X} from 'lucide-react';

export function Modal({title,onClose,children,wide=false}){
 const dialog=useRef(null),close=useRef(onClose);close.current=onClose;
 useEffect(()=>{
  const el=dialog.current,backdrop=el.parentElement,opener=document.activeElement;
  const siblings=[...backdrop.parentElement.children].filter(n=>n!==backdrop&&!n.contains(backdrop));
  const previous=siblings.map(n=>[n,n.inert,n.getAttribute('aria-hidden')]);
  previous.forEach(([n])=>{n.inert=true;n.setAttribute('aria-hidden','true')});
  const focusable=()=>[...el.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]')].filter(n=>n.getClientRects().length);
  const first=()=>{(focusable()[0]||el).focus({preventScroll:true})};
  const key=e=>{if(e.key==='Escape'){e.preventDefault();close.current();return}if(e.key!=='Tab')return;const list=focusable();if(!list.length){e.preventDefault();el.focus();return}const i=list.indexOf(document.activeElement);if(e.shiftKey&&i<=0){e.preventDefault();list.at(-1).focus()}else if(!e.shiftKey&&(i===list.length-1||i<0)){e.preventDefault();list[0].focus()}};
  const focus=e=>{if(!el.contains(e.target))first()};
  document.addEventListener('keydown',key,true);document.addEventListener('focusin',focus,true);first();
  return()=>{document.removeEventListener('keydown',key,true);document.removeEventListener('focusin',focus,true);previous.forEach(([n,inert,hidden])=>{n.inert=inert;if(hidden===null)n.removeAttribute('aria-hidden');else n.setAttribute('aria-hidden',hidden)});if(opener?.isConnected&&!opener.inert)opener.focus({preventScroll:true})};
 },[]);
 return <div className="modal-backdrop" onClick={()=>close.current()}><section ref={dialog} tabIndex={-1} className={'modal '+(wide?'wide':'')} role="dialog" aria-modal="true" aria-label={title} onClick={e=>e.stopPropagation()}><div className="modal-head"><h2>{title}</h2><button className="icon-btn" title="بستن" aria-label="بستن" onClick={()=>close.current()}><X size={20}/></button></div>{children}</section></div>;
}
