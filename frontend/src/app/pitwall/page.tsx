import { LivePitWall } from "@/components/live-pitwall";
import { SurfaceError } from "@/components/status";
import { getLivePitWall,getLiveRace,getLiveStatus } from "@/lib/api/live";
import { getRaceControl,getWeather } from "@/lib/api/weekend";

export const dynamic="force-dynamic";

export default async function PitWall(){
  const statusResult=await getLiveStatus().then(data=>({data})).catch((error:Error)=>({error}));
  if(!("data" in statusResult)) return <div className="page"><SurfaceError title="Live status unavailable" detail={statusResult.error.message}/></div>;
  const status=statusResult.data;
  if(!status.live){
    return <LivePitWall initialStatus={status}/>;
  }
  const [race,pitwall,weather,control]=await Promise.allSettled([getLiveRace(),getLivePitWall(),getWeather(),getRaceControl()]);
  return <LivePitWall initialStatus={status} initialRace={race.status==="fulfilled"?race.value:null} initialPitwall={pitwall.status==="fulfilled"?pitwall.value:null} initialWeather={weather.status==="fulfilled"?weather.value:null} initialControl={control.status==="fulfilled"?control.value:null}/>;
}

