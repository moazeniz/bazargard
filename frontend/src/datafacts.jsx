import React,{useEffect,useState} from 'react';
import {call} from './features';

const n=v=>v==null?'—':Number(v).toLocaleString('fa-IR');
const pct=(a,b)=>b?Math.round(100*a/b).toLocaleString('fa-IR')+'٪':'—';
const AGE={'≤30 days':'تا ۳۰ روز','≤90 days':'۳۱ تا ۹۰ روز','≤365 days':'۹۱ روز تا یک سال','>1 year':'بیش از یک سال','≤7 days':'تا ۷ روز','unknown':'نامشخص'};

// Key numbers of the Torob catalogue, computed from the database itself (/api/dataset-facts).
export function DataFacts(){
 const [d,setD]=useState(null),[error,setError]=useState('');
 useEffect(()=>{let live=true;call('dataset-facts').then(x=>{if(live)setD(x)}).catch(e=>{if(live)setError(e.message)});return()=>{live=false}},[]);
 if(error)return <p className="muted">{error}</p>;
 if(!d)return <p className="muted">در حال خواندن آمار دیتاست…</p>;
 const s=d.shops,o=d.offers,p=d.products,ages=o.price_age||{},totalAges=Object.values(ages).reduce((a,b)=>a+b,0);
 const tiles=[['فروشگاه',n(s.total),'همه با مختصات روی نقشه'],['فروشگاه با نام واقعی',n(s.real_identity),'دارای وب‌سایت ('+n(s.website)+') یا اینستاگرام ('+n(s.instagram)+')'],
  ['آفر قیمت‌دار',n(o.total),'میانه '+n(o.per_shop_median)+' آفر در هر فروشگاه'],['محصول',n(p.total),n(p.categories)+' دسته'],
  ['ساعت کاری اعلام‌شده',pct(s.with_declared_hours,s.total),n(s.with_declared_hours)+' فروشگاه'],['اعتبار و اقساط حضوری',n(s.with_in_person_credit),'فروشگاه با شرایط حضوری'],
  ['محدوده‌ها',n(s.areas),'بیشترین: '+(s.top_areas||[]).slice(0,3).map(a=>a[0]).join('، ')],['سقف هر فروشگاه',n(o.products_per_shop_max),'محصول ('+n(o.shops_at_300_products)+' فروشگاه در سقف)']];
 return <section className="data-facts" aria-label="آمار دیتاست ترب">
  <div className="df-grid">{tiles.map(([t,v,s])=><div key={t} className="df-tile"><span>{t}</span><b>{v}</b><small>{s}</small></div>)}</div>
  <div className="df-ages"><h3>عمر قیمت‌ها نسبت به زمان ورود داده</h3>{Object.entries(AGE).filter(([k])=>ages[k]).map(([k,label])=><div key={k} className="df-bar"><span>{label}</span><i><b style={{width:(100*ages[k]/totalAges)+'%'}}/></i><small>{pct(ages[k],totalAges)}</small></div>)}</div>
  <ul className="df-caveats">
   <li>نام {n(s.total-s.real_identity)} فروشگاه و همه شماره تلفن‌ها ساختگی است؛ کارت‌ها برچسب «نام ساختگی» یا «هویت منبع» دارند و هیچ تماس خودکاری انجام نمی‌شود.</li>
   <li>قیمت‌ها ثبت‌شده در ترب‌اند، نه زنده؛ تاریخ آخرین تغییر قیمت کنار هر آفر نمایش داده می‌شود و موجودی تا گزارش فروشنده «نامشخص» است.</li>
   <li>{n(o.price_below_1000_toman)} آفر قیمت کمتر از هزار تومان و {n(o.price_above_10b_toman)} آفر قیمت بالای ده میلیارد دارند؛ این‌ها خطای داده‌اند و در «بررسی آفر» علامت می‌خورند.</li>
   <li>پوشش محدود است: نبود نتیجه در دیتاست به معنی نبود کالا در بازار نیست.</li>
  </ul>
  <small className="df-source">منبع: {d.source} · ورود داده: {new Date(d.imported_at).toLocaleDateString('fa-IR')}</small>
 </section>;
}
