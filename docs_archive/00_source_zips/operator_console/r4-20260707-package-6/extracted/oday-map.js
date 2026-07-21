/* OdayMap v2 — Leaflet wrapper for Oday Plus Network workbench.
   Props: markers[{id,lat,lng,label,sel,shape:circle|square|diamond,color}], zones[{id,lat,lng,r,color,sel,label}],
   comps(bool), fitSig(string), onSelect(id), onZoneSelect(id). Requires window.L (loaded in helmet). */
(function(){
  var R=window.React;
  var COMP=[[25.0341,121.5641],[25.0308,121.5688],[25.0262,121.5408],[25.0092,121.4566],[25.0942,121.5266],[24.9585,121.2422],[25.0372,121.4472],[25.0551,121.4409],[24.9601,121.2381]];
  window.OdayMap=function(props){
    var ref=R.useRef(null);
    var S=R.useRef({map:null,mk:null,zn:null,cp:null,lm:'',lz:'',lc:'',lf:''});
    var rs=R.useState(!!window.L),ready=rs[0],setReady=rs[1];
    R.useEffect(function(){
      if(window.L){setReady(true);return;}
      var t=setInterval(function(){if(window.L){clearInterval(t);setReady(true);}},120);
      return function(){clearInterval(t);};
    },[]);
    R.useEffect(function(){
      if(!ready||!ref.current||S.current.map)return;
      var map=window.L.map(ref.current,{zoomControl:true,scrollWheelZoom:true});
      window.L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',{attribution:'&copy; OpenStreetMap &copy; CARTO',maxZoom:19}).addTo(map);
      map.setView([25.02,121.47],11);
      S.current.zn=window.L.layerGroup().addTo(map);
      S.current.cp=window.L.layerGroup().addTo(map);
      S.current.mk=window.L.layerGroup().addTo(map);
      S.current.map=map;
      setTimeout(function(){map.invalidateSize();},300);
      setTimeout(function(){map.invalidateSize();},900);
    },[ready]);
    R.useEffect(function(){
      var C=S.current,map=C.map;
      if(!map)return;
      var ms=props.markers||[],zs=props.zones||[];
      var mj=JSON.stringify(ms.map(function(m){return [m.id,m.lat,m.lng,m.color,m.shape,m.sel,m.label];}));
      if(mj!==C.lm){
        C.lm=mj;C.mk.clearLayers();
        ms.forEach(function(m){
          if(m.lat==null||m.lng==null)return;
          var shape=m.shape==='diamond'?'border-radius:3px;transform:rotate(45deg);':(m.shape==='square'?'border-radius:4px;':'border-radius:50%;');
          var ring=m.sel?'box-shadow:0 0 0 4px rgba(46,58,151,.32);':'box-shadow:0 1px 4px rgba(20,26,50,.3);';
          var html='<div style="display:flex;flex-direction:column;align-items:center;"><span style="width:14px;height:14px;background:'+m.color+';border:2px solid #fff;'+shape+ring+'display:block;"></span>'+(m.label?'<span style="margin-top:2px;font:700 10px \'Noto Sans TC\',sans-serif;color:#3A4362;background:rgba(255,255,255,.92);border-radius:4px;padding:0 5px;white-space:nowrap;box-shadow:0 1px 3px rgba(20,26,50,.12);">'+m.label+'</span>':'')+'</div>';
          var mk=window.L.marker([m.lat,m.lng],{icon:window.L.divIcon({html:html,className:'',iconSize:[14,14],iconAnchor:[7,7]})});
          mk.on('click',function(){if(props.onSelect)props.onSelect(m.id);});
          C.mk.addLayer(mk);
        });
      }
      var zj=JSON.stringify(zs);
      if(zj!==C.lz){
        C.lz=zj;C.zn.clearLayers();
        zs.forEach(function(z){
          var c=window.L.circle([z.lat,z.lng],{radius:z.r||800,color:z.color,weight:z.sel?2.6:1.2,opacity:z.sel?.9:.5,fillColor:z.color,fillOpacity:z.sel?.22:.13});
          c.on('click',function(){if(props.onZoneSelect)props.onZoneSelect(z.id);});
          C.zn.addLayer(c);
          if(z.label){
            var lb=window.L.marker([z.lat,z.lng],{icon:window.L.divIcon({html:'<span style="font:700 10.5px \'Noto Sans TC\',sans-serif;color:#fff;background:'+z.color+';border-radius:999px;padding:2px 9px;white-space:nowrap;box-shadow:0 2px 6px rgba(20,26,50,.25);">'+z.label+'</span>',className:'',iconSize:[10,10],iconAnchor:[5,5]})});
            lb.on('click',function(){if(props.onZoneSelect)props.onZoneSelect(z.id);});
            C.zn.addLayer(lb);
          }
        });
      }
      var wc=props.comps?'1':'0';
      if(wc!==C.lc){
        C.lc=wc;C.cp.clearLayers();
        if(props.comps)COMP.forEach(function(p){C.cp.addLayer(window.L.circleMarker(p,{radius:4,color:'#5A6478',weight:1,opacity:.7,fillColor:'#7A83AC',fillOpacity:.8}));});
      }
      var fs=String(props.fitSig||'');
      if(fs&&fs!==C.lf){
        C.lf=fs;
        var pts=[];
        zs.forEach(function(z){pts.push([z.lat,z.lng]);});
        ms.forEach(function(m){if(m.lat!=null&&m.lng!=null)pts.push([m.lat,m.lng]);});
        if(pts.length)setTimeout(function(){map.invalidateSize();map.fitBounds(pts,{padding:[36,36]});},240);
      }
    });
    return R.createElement('div',{ref:ref,style:{position:'absolute',inset:'0',zIndex:0}});
  };
})();
