import { expect, test } from "@playwright/test";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";

const target="ap-network-3319";
const tabs=["decisions","audit","userManagement","evidencePackage","statusBoard","featureFlags","learningHub","approvals"];

for(const width of [1440,1024,390]){
  test(`Governance requested target, URL state and browser history at ${width}`,async({page},info)=>{
    test.setTimeout(120_000);
    await page.setViewportSize({width,height:900});
    await page.addInitScript(()=>sessionStorage.setItem("oday.operator.role","ops-lead"));
    let writes=0;
    page.on("request",request=>{if(request.method()==="POST"&&/\/governance\/(?:decisions|evidence-packages)/.test(request.url()))writes+=1;});
    await page.goto(`/operator?ws=govern&tab=approvals&entity=${target}&evidence=ev-score&trace=local-governance-check`);
    const detail=page.getByRole("article",{name:"Approval detail"});
    await expect(detail.getByRole("heading",{name:"Approve SiteScore override"})).toBeVisible();
    const headerGroupGeometry=await page.getByTestId("operator-topbar-inner").evaluate(element=>{
      const brand=element.querySelector('[aria-label="Oday Plus Operator Console"]');
      const workspaceNav=element.querySelector('nav[aria-label="Operator workspaces"]');
      const approvalCount=element.querySelector('[data-testid="operator-approval-count"]');
      const actions=Array.from(element.children).find(child=>approvalCount!==null && child.contains(approvalCount));
      if(!brand || !workspaceNav || !actions) throw new Error("HEADER_GROUPS_NOT_FOUND");
      const box=(node:Element)=>{const r=node.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};};
      return {brand:box(brand),workspaceNav:box(workspaceNav),actions:box(actions)};
    });
    const {brand,workspaceNav,actions}=headerGroupGeometry;
    for(const group of [brand,workspaceNav,actions]) {
      expect(group.width).toBeGreaterThan(0);expect(group.height).toBeGreaterThan(0);
      expect(group.x).toBeGreaterThanOrEqual(0);
      expect(group.x+group.width).toBeLessThanOrEqual(width);
    }
    if(width>768) {
      expect(Math.min(brand.y+brand.height,workspaceNav.y+workspaceNav.height)).toBeGreaterThan(Math.max(brand.y,workspaceNav.y));
      expect(workspaceNav.x).toBeGreaterThanOrEqual(brand.x+brand.width);
      if(width>1240) {
        expect(Math.min(workspaceNav.y+workspaceNav.height,actions.y+actions.height)).toBeGreaterThan(Math.max(workspaceNav.y,actions.y));
        expect(actions.x).toBeGreaterThanOrEqual(workspaceNav.x+workspaceNav.width);
      } else expect(actions.y).toBeGreaterThanOrEqual(Math.max(brand.y+brand.height,workspaceNav.y+workspaceNav.height));
    } else {
      expect(workspaceNav.y).toBeGreaterThanOrEqual(brand.y+brand.height);
      expect(actions.y).toBeGreaterThanOrEqual(workspaceNav.y+workspaceNav.height);
    }
    await expect(page.getByRole("navigation",{name:"Governance tabs"}).getByRole("button")).toHaveCount(3);
    await expect(page.getByTestId("governance-selected-evidence")).toContainText("ev-score");
    await page.reload();
    await expect(detail.getByRole("heading",{name:"Approve SiteScore override"})).toBeVisible();
    const states=[];
    for(const tab of tabs){
      if(tab === "userManagement") {
        // Enter the existing non-R7 extension via its established deep link;
        // preserve every tab/URL/history assertion after that entry.
        await page.goto(`/operator?ws=govern&tab=userManagement&entity=${target}&evidence=ev-score&trace=local-governance-check`);
      }
      await page.getByTestId(`governance-tab-${tab}`).click();
      await expect(page.getByTestId(`governance-tab-${tab}`)).toHaveAttribute("aria-current","page");
      await expect(page).toHaveURL(new RegExp(`tab=${tab}(?:&|$)`));
      expect(new URL(page.url()).searchParams.get("entity")).toBe(target);
      expect(new URL(page.url()).searchParams.get("trace")).toBe("local-governance-check");
      states.push({tab,url:page.url()});
    }
    await page.goBack();
    await expect(page.getByTestId("governance-tab-learningHub")).toHaveAttribute("aria-current","page");
    await page.goForward();
    await expect(page.getByTestId("governance-tab-approvals")).toHaveAttribute("aria-current","page");
    await expect(detail.getByRole("heading",{name:"Approve SiteScore override"})).toBeVisible();
    await page.getByTestId("governance-tab-audit").click();
    await page.getByRole("button",{name:"影像調閱",exact:true}).click();
    await expect(page).toHaveURL(/auditCategory=camera/);
    await page.reload();
    await expect(page.getByTestId("governance-tab-audit")).toHaveAttribute("aria-current","page");
    await expect(page.getByRole("button",{name:"影像調閱",exact:true})).toHaveAttribute("aria-current","true");
    await page.goto("/operator?ws=govern&tab=approvals&entity=NOT-IN-SCOPE");
    await expect(page.getByTestId("governance-approval-unavailable")).toContainText("指定核准項目未取得");
    await expect(detail.getByRole("button",{name:"核准",exact:true})).toHaveCount(0);
    await page.goto(`/operator?ws=govern&tab=approvals&entity=${target}&evidence=NOT-IN-SCOPE`);
    await expect(page.getByTestId("governance-evidence-unavailable")).toBeVisible();
    await expect(page.getByTestId("governance-selected-evidence")).toHaveCount(0);
    const geometry=await page.getByTestId("governance-workspace").evaluate(element=>({
      rect:element.getBoundingClientRect().toJSON(),documentWidth:document.documentElement.scrollWidth,viewport:innerWidth,
    }));
    expect(geometry.documentWidth).toBeLessThanOrEqual(width);
    expect(writes).toBe(0);
    const evidence=process.env.STORE_OPS_PARITY_EVIDENCE_DIR??info.outputDir;await mkdir(evidence,{recursive:true});
    await page.screenshot({path:path.join(evidence,`governance-navigation-${width}.png`),animations:"disabled"});
    await writeFile(path.join(evidence,`governance-navigation-${width}.json`),JSON.stringify({states,geometry,writes,scope:"Synthetic local URL/history checks; not remote visual approval"},null,2));
  });
}
