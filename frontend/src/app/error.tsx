"use client";
import { SurfaceError } from "@/components/status";
export default function ErrorPage({error,reset}:{error:Error;reset:()=>void}){return <div className="page"><SurfaceError title="Surface unavailable" detail={error.message}/><button className="button-link error-retry" onClick={reset}>Try again</button></div>}
