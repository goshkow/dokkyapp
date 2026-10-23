using System;
using System.Windows.Forms;
using System.Drawing;
public class DokkyFixture {
    [STAThread] public static void Main() {
        var window=new Form(); window.Text="Dokky Computer Use Fixture"; window.Size=new Size(520,400);
        var button=new Button(); button.Text="Count 0"; button.SetBounds(30,30,160,40);
        int count=0; button.Click+=(s,e)=>button.Text="Count "+(++count);
        var input=new TextBox(); input.SetBounds(30,90,440,30);
        var label=new Label(); label.Text="Typed: empty";label.SetBounds(30,135,440,30);
        input.TextChanged+=(s,e)=>label.Text="Typed: "+input.Text;
        var list=new ListBox();list.SetBounds(30,175,440,140);
        for(int i=1;i<=50;i++) list.Items.Add("Fixture line "+i);
        window.Controls.AddRange(new Control[]{button,input,label,list});
        Application.Run(window);
    }
}
