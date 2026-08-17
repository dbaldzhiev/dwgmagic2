using Autodesk.AutoCAD.ApplicationServices;
using Autodesk.AutoCAD.DatabaseServices;
using Autodesk.AutoCAD.EditorInput;
using Autodesk.AutoCAD.Geometry;
using Autodesk.AutoCAD.Runtime;
using System.Collections.Generic;

// This line is not mandatory, but improves loading performances
[assembly: CommandClass(typeof(tecCommands.tecCommands))]

namespace tecCommands
{
    public class tecCommands
    {
        [CommandMethod("TecRnXref")]
        public static void TecRnXref()
        {
            Document doc = Application.DocumentManager.MdiActiveDocument;
            Database db = doc.Database;
            Editor ed = doc.Editor;

            using (Transaction tx = db.TransactionManager.StartTransaction())
            {
                XrefGraph xrgraph = db.GetHostDwgXrefGraph(false);

                for (int i = 1; i < xrgraph.NumNodes; i++)
                {
                    XrefGraphNode xrNode = xrgraph.GetXrefNode(i);

                    if (!xrNode.IsNested)
                    {
                        BlockTableRecord btr = (BlockTableRecord)tx.GetObject(xrNode.BlockTableRecordId, OpenMode.ForWrite);
                        string fileName = System.IO.Path.GetFileNameWithoutExtension(btr.PathName);
                        db.XrefEditEnabled = true;
                        ed.WriteMessage($"CHANGING XREF NAME: {btr.Name} TO {fileName}\n");
                        btr.Name = fileName;
                    }
                }

                tx.Commit();
            }
        }

        [CommandMethod("tecArXref")]
        public static void tecArXref()
        {
            Document doc = Application.DocumentManager.MdiActiveDocument;
            Database db = doc.Database;
            Editor ed = doc.Editor;

            using (Transaction tx = db.TransactionManager.StartTransaction())
            {
                BlockTableRecord ms = (BlockTableRecord)tx.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);
                RXObject brClass = RXObject.GetClass(typeof(BlockReference));

                Point3d lastrightpt = new Point3d(0, 0, 0);

                foreach (ObjectId id in ms)
                {
                    if (id.ObjectClass == brClass)
                    {
                        BlockReference br = (BlockReference)tx.GetObject(id, OpenMode.ForWrite);
                        ed.WriteMessage($"NAME: {br.Name}\n");
                        Extents3d bounds = br.GeometricExtents;
                        ed.WriteMessage($"BOUNDS: {bounds}\n");

                        Vector3d vec = lastrightpt - bounds.MinPoint;
                        ed.WriteMessage($"VECTOR: {vec}\n");

                        Point3d rightpt = new Point3d(bounds.MaxPoint.X, bounds.MinPoint.Y, 0);
                        Vector3d newrp = vec + rightpt.GetAsVector() + new Vector3d(50, 0, 0);
                        lastrightpt = new Point3d(newrp.X, newrp.Y, 0);
                        ed.WriteMessage($"NEWLASTRIGHT SET to {lastrightpt}\n");

                        Matrix3d mat = Matrix3d.Displacement(vec);
                        br.TransformBy(mat);
                    }
                }

                tx.Commit();
            }
        }

        public static bool IsInside2D(Polyline vpInms, Point3d entPoint)
        {
            Extents3d fence = vpInms.GeometricExtents;
            return entPoint.X > fence.MinPoint.X && entPoint.X < fence.MaxPoint.X && entPoint.Y > fence.MinPoint.Y && entPoint.Y < fence.MaxPoint.Y;
        }

        [CommandMethod("tecFixMs")]
        public void tecFixMs()
        {
            Document doc = Application.DocumentManager.MdiActiveDocument;
            Database db = doc.Database;
            Editor ed = doc.Editor;
            LayoutManager layoutMan = LayoutManager.Current;

            using (Transaction tx = db.TransactionManager.StartTransaction())
            {
                DBDictionary layoutDict = (DBDictionary)db.LayoutDictionaryId.GetObject(OpenMode.ForWrite);
                BlockTableRecord ms = (BlockTableRecord)tx.GetObject(SymbolUtilityServices.GetBlockModelSpaceId(db), OpenMode.ForRead);

                foreach (DBDictionaryEntry entry in layoutDict)
                {
                    string layoutName = entry.Key;
                    if (layoutName == "Layout1")
                    {
                        ed.WriteMessage("---------------\n");
                        Layout layoutObj = (Layout)layoutMan.GetLayoutId(layoutName).GetObject(OpenMode.ForWrite);
                        ed.WriteMessage($"{layoutObj.LayoutName} - Viewports: {layoutObj.GetViewports().Count}\n");

                        Vector3d oshift = new Vector3d(0, 0, 0);
                        Point3d lastvpcpt = new Point3d(0, 0, 0);
                        int counter = 0;
                        Point3d psbasept = new Point3d(0, 0, 0);
                        Point3d msbasept = new Point3d(0, 0, 0);

                        foreach (ObjectId vpId in layoutObj.GetViewports())
                        {
                            Viewport vp = (Viewport)vpId.GetObject(OpenMode.ForWrite);
                            ed.WriteMessage($"VP # - {vp.Number} - center: {vp.CenterPoint} view center: {vp.ViewCenter}\n");

                            Polyline vpOutlineInMs = CreateViewportOutline(vp, tx);

                            foreach (ObjectId entId in ms)
                            {
                                Entity ent = (Entity)tx.GetObject(entId, OpenMode.ForWrite);
                                try
                                {
                                    Extents3d entExtents = ent.GeometricExtents;
                                    Point3d cpt = new Point3d(
                                        entExtents.MinPoint.X + (entExtents.MaxPoint.X - entExtents.MinPoint.X) / 2,
                                        entExtents.MinPoint.Y + (entExtents.MaxPoint.Y - entExtents.MinPoint.Y) / 2, 0);

                                    if (IsInside2D(vpOutlineInMs, cpt))
                                    {
                                        Matrix3d vptargettransform = CreateViewportTransform(vp, ref counter, ref msbasept, ref psbasept);
                                        ent.TransformBy(vptargettransform);
                                        vp.ViewTarget = vp.ViewTarget.TransformBy(vptargettransform);
                                    }
                                }
                                catch (Autodesk.AutoCAD.Runtime.Exception) { }
                            }
                        }
                    }
                }

                tx.Commit();
            }
        }

        private static Polyline CreateViewportOutline(Viewport vp, Transaction tx)
        {
            Polyline vpOutlineInMs;
            if (vp.NonRectClipOn)
            {
                Entity vpBoundary = (Entity)tx.GetObject(vp.NonRectClipEntityId, OpenMode.ForRead);
                vpOutlineInMs = (Polyline)vpBoundary.Clone();
            }
            else
            {
                Extents3d vpExt = vp.GeometricExtents;
                vpOutlineInMs = new Polyline(4);
                vpOutlineInMs.AddVertexAt(0, new Point2d(vpExt.MinPoint.X, vpExt.MinPoint.Y), 0, 0, 0);
                vpOutlineInMs.AddVertexAt(1, new Point2d(vpExt.MaxPoint.X, vpExt.MinPoint.Y), 0, 0, 0);
                vpOutlineInMs.AddVertexAt(2, new Point2d(vpExt.MaxPoint.X, vpExt.MaxPoint.Y), 0, 0, 0);
                vpOutlineInMs.AddVertexAt(3, new Point2d(vpExt.MinPoint.X, vpExt.MaxPoint.Y), 0, 0, 0);
                vpOutlineInMs.Closed = true;
            }

            Point3d vpMScpt = new Point3d(vp.ViewCenter.X, vp.ViewCenter.Y, 0.0);
            Point3d vpPScpt = vp.CenterPoint;
            Matrix3d msToPs = Matrix3d.Displacement(vpPScpt - vpMScpt) *
                              Matrix3d.Scaling(vp.CustomScale, vpMScpt) *
                              Matrix3d.Rotation(vp.TwistAngle, Vector3d.ZAxis, Point3d.Origin) *
                              Matrix3d.WorldToPlane(new Plane(vp.ViewTarget, vp.ViewDirection));
            vpOutlineInMs.TransformBy(msToPs.Inverse());

            return vpOutlineInMs;
        }

        private static Matrix3d CreateViewportTransform(Viewport vp, ref int counter, ref Point3d msbasept, ref Point3d psbasept)
        {
            Matrix3d vptargettransform;
            Point3d vpMScpt = new Point3d(vp.ViewCenter.X, vp.ViewCenter.Y, 0.0);
            Point3d vpPScpt = vp.CenterPoint;

            if (counter == 0)
            {
                counter = 1;
                vptargettransform = Matrix3d.Displacement(vpMScpt.GetVectorTo(msbasept));
            }
            else
            {
                Matrix3d bpmat = Matrix3d.Displacement(psbasept.GetVectorTo(vpPScpt).TransformBy(Matrix3d.Scaling(1 / vp.CustomScale, vpPScpt)));
                msbasept = msbasept.TransformBy(bpmat);
                vptargettransform = Matrix3d.Displacement(vpMScpt.GetVectorTo(msbasept));
            }

            psbasept = vpPScpt;
            return vptargettransform;
        }

        [CommandMethod("tecTest")]
        public static void tecTest()
        {
            Document doc = Application.DocumentManager.MdiActiveDocument;
            Database db = doc.Database;
            Editor ed = doc.Editor;
            ed.WriteMessage("HELLO! TEST! DEBUG TECTONICA!\n");
        }

        [CommandMethod("tecBXT")]
        public void BindXrefsT()
        {
            Document doc = Application.DocumentManager.MdiActiveDocument;
            Editor ed = doc.Editor;
            Database db = doc.Database;
            ObjectIdCollection xrefCollection = new ObjectIdCollection();
            List<string> xrefNames = new List<string>();

            using (XrefGraph xg = db.GetHostDwgXrefGraph(false))
            {
                for (int i = 0; i < xg.NumNodes; i++)
                {
                    XrefGraphNode xNode = xg.GetXrefNode(i) as XrefGraphNode;
                    if (!xNode.Database.Filename.Equals(db.Filename) && xNode.XrefStatus == XrefStatus.Resolved)
                    {
                        xrefCollection.Add(xNode.BlockTableRecordId);
                        xrefNames.Add(xNode.Name);
                    }
                }
            }

            if (xrefCollection.Count != 0)
            {
                db.BindXrefs(xrefCollection, true);
            }

            foreach (string s in xrefNames)
            {
                ed.WriteMessage($"\n {s}");
                explodeAllXrefBlocks(s);
            }
        }

        public static void explodeAllXrefBlocks(string xrefName)
        {
            Document doc = Application.DocumentManager.MdiActiveDocument;
            Editor ed = doc.Editor;
            Database db = doc.Database;

            using (Transaction tr = db.TransactionManager.StartTransaction())
            {
                BlockTable bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                BlockTableRecord btr = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

                string xrefprefix = xrefName.Split('$')[0];

                foreach (ObjectId id in btr)
                {
                    Entity ent = tr.GetObject(id, OpenMode.ForRead) as Entity;
                    if (ent is BlockReference br && br.Name.Contains(xrefprefix))
                    {
                        DBObjectCollection objs = new DBObjectCollection();
                        br.UpgradeOpen();
                        br.Explode(objs);
                        br.Erase();
                        drawDBObjectCollection(objs);
                    }
                }

                tr.Commit();
            }
        }

        public static void drawDBObjectCollection(DBObjectCollection objs)
        {
            Database db = HostApplicationServices.WorkingDatabase;

            using (Transaction tr = db.TransactionManager.StartTransaction())
            {
                BlockTable bt = (BlockTable)tr.GetObject(db.BlockTableId, OpenMode.ForRead);
                BlockTableRecord btr = (BlockTableRecord)tr.GetObject(bt[BlockTableRecord.ModelSpace], OpenMode.ForWrite);

                foreach (DBObject obj in objs)
                {
                    if (obj is Entity ent)
                    {
                        btr.AppendEntity(ent);
                        tr.AddNewlyCreatedDBObject(ent, true);
                    }
                }

                tr.Commit();
            }
        }
    }
}
